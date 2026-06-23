import io
import logging

import pyarrow as pa
import pyarrow.parquet as pq
from google.cloud import bigquery

logger = logging.getLogger(__name__)

PROJECT_ID = "gds-social-data"


def load_data_to_partitioned_bq(
    table: pa.Table,
    table_name: str,
    dataset_name: str,
    job_schema: dict[str, pa.DataType],
    time_partition_field: str = "day",
) -> int:
    """Casts a pa.Table to the given schema and writes it to a time-partitioned BigQuery table.
    Infers partition dates from the time_partition_field column.
    Creates the table if it does not exist. Overwrites each partition present in the data.
    Returns the total number of rows loaded."""
    schema = pa.schema([(key, job_schema[key]) for key in job_schema])
    cast_table = table.cast(schema)
    base_table_id = f"{PROJECT_ID}.{dataset_name}.{table_name}"
    client = bigquery.Client(project=PROJECT_ID)
    if time_partition_field == "month":
        partition_type = bigquery.TimePartitioningType.MONTH
    else:
        partition_type = bigquery.TimePartitioningType.DAY
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.PARQUET,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        time_partitioning=bigquery.table.TimePartitioning(type_=partition_type, field=time_partition_field),
    )
    unique_dates = sorted(cast_table.column(time_partition_field).unique().to_pylist(), reverse=True)
    total_rows = 0
    try:
        for partition_date in unique_dates:
            partition_table = cast_table.filter(
                pa.compute.equal(cast_table.column(time_partition_field), partition_date)
            )
            if time_partition_field == "month":
                partition_str = partition_date.strftime("%Y%m")
            elif hasattr(partition_date, "strftime"):
                partition_str = partition_date.strftime("%Y%m%d")
            else:
                partition_str = str(partition_date).replace("-", "")
            destination_table_id = f"{base_table_id}${partition_str}"
            buf = io.BytesIO()
            pq.write_table(partition_table, buf)
            buf.seek(0)
            job = client.load_table_from_file(buf, destination_table_id, job_config=job_config)
            job.result()
            logger.info("Loaded %d rows to %s", partition_table.num_rows, destination_table_id)
            total_rows += partition_table.num_rows
        return total_rows
    except Exception:
        logger.exception("Failed to load to %s", base_table_id)
        raise


def load_data_to_partitioned_bq_merge(
    table: pa.Table,
    table_name: str,
    dataset_name: str,
    job_schema: dict[str, pa.DataType],
    merge_keys: list[str],
    time_partition_field: str = "day",
) -> int:
    """Loads data to a partitioned BigQuery table using a staging table with MERGE
    to upsert rows without overwriting existing partition data.

    Data is first loaded into a staging table, then a MERGE statement matches on
    merge_keys to update existing rows and insert new ones.

    Args:
        table: The PyArrow table to load.
        table_name: The target BigQuery table name.
        dataset_name: The BigQuery dataset name.
        job_schema: Column name to PyArrow type mapping for schema casting.
        merge_keys: Columns that define row uniqueness for deduplication.
        time_partition_field: The column used for time partitioning.

    Returns:
        The total number of rows loaded.
    """
    schema = pa.schema([(key, job_schema[key]) for key in job_schema])
    cast_table = table.cast(schema)
    target_table_id = f"{PROJECT_ID}.{dataset_name}.{table_name}"
    staging_table_id = f"{PROJECT_ID}.{dataset_name}.{table_name}_staging"
    client = bigquery.Client(project=PROJECT_ID)

    staging_job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.PARQUET,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        time_partitioning=bigquery.table.TimePartitioning(field=time_partition_field),
    )
    buf = io.BytesIO()
    pq.write_table(cast_table, buf)
    buf.seek(0)
    job = client.load_table_from_file(buf, staging_table_id, job_config=staging_job_config)
    job.result()
    logger.info("Loaded %d rows to staging table %s", cast_table.num_rows, staging_table_id)

    try:
        client.get_table(target_table_id)
    except Exception:
        staging_table_ref = client.get_table(staging_table_id)
        target_table = bigquery.Table(target_table_id, schema=staging_table_ref.schema)
        target_table.time_partitioning = bigquery.table.TimePartitioning(field=time_partition_field)
        client.create_table(target_table)
        logger.info("Created target table %s", target_table_id)

    columns = list(job_schema.keys())
    on_clause = " AND ".join(f"T.{k} = S.{k}" for k in merge_keys)
    update_cols = [c for c in columns if c not in merge_keys]
    update_set = ", ".join(f"T.{c} = S.{c}" for c in update_cols)
    insert_columns = ", ".join(columns)
    insert_values = ", ".join(f"S.{c}" for c in columns)

    merge_sql = f"""
    MERGE `{target_table_id}` T
    USING `{staging_table_id}` S
    ON {on_clause}
    WHEN MATCHED THEN
      UPDATE SET {update_set}
    WHEN NOT MATCHED THEN
      INSERT ({insert_columns})
      VALUES ({insert_values})
    """

    try:
        client.query(merge_sql).result()
        logger.info("Merged %d rows into %s", cast_table.num_rows, target_table_id)
        return cast_table.num_rows
    except Exception:
        logger.exception("Failed to merge into %s", target_table_id)
        raise
    finally:
        client.delete_table(staging_table_id, not_found_ok=True)
        logger.info("Cleaned up staging table %s", staging_table_id)


def load_data_to_bq(table: pa.Table, table_name: str, dataset_name: str, job_schema: dict[str, pa.DataType]) -> int:
    """Casts a pa.Table to the given schema and writes it to a non-partitioned BigQuery table.
    Creates the table if it does not exist. Overwrites existing data.
    Returns the number of rows loaded."""
    schema = pa.schema([(key, job_schema[key]) for key in job_schema])
    cast_table = table.cast(schema)
    destination_table_id = f"{PROJECT_ID}.{dataset_name}.{table_name}"
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.PARQUET,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    try:
        client = bigquery.Client(project=PROJECT_ID)
        buf = io.BytesIO()
        pq.write_table(cast_table, buf)
        buf.seek(0)
        job = client.load_table_from_file(buf, destination_table_id, job_config=job_config)
        job.result()
        logger.info("Loaded %d rows to %s", cast_table.num_rows, destination_table_id)
        return cast_table.num_rows
    except Exception:
        logger.exception("Failed to load to %s", destination_table_id)
        raise


def retrieve_data_from_bq(PROJECT_ID: str, dataset_name: str, table_name: str) -> pa.Table:
    """Reads all rows from a BigQuery table and returns them as a pa.Table."""
    destination_table_id = f"{PROJECT_ID}.{dataset_name}.{table_name}"
    query = f"SELECT * FROM `{destination_table_id}`"
    try:
        client = bigquery.Client(project=PROJECT_ID)
        return client.query(query).to_arrow()
    except Exception:
        logger.exception("Failed to retrieve data from %s", destination_table_id)
        raise
