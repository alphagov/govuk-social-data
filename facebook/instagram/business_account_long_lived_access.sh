#!/bin/bash

# --- Configuration ---
API_VERSION="v25.0"
TARGET_PAGE_NAME="govuk"

echo "Fetching secrets from Secret Manager..."

LONG_LIVED_TOKEN=$(gcloud secrets versions access latest --secret="instagram_bearer_token" 2>/dev/null)
APP_ID=$(gcloud secrets versions access latest --secret="instagram_app_id" 2>/dev/null)
APP_SECRET=$(gcloud secrets versions access latest --secret="instagram_app_secret" 2>/dev/null)

if [ -z "$LONG_LIVED_TOKEN" ] || [ -z "$APP_ID" ] || [ -z "$APP_SECRET" ]; then
    echo "Error: Failed to retrieve one or more secrets from Secret Manager."
    echo "Ensure you are authenticated: gcloud auth login"
    exit 1
fi

echo "Secrets retrieved. Fetching Facebook Pages..."

PAGES_RESPONSE=$(curl -s -G "https://graph.facebook.com/$API_VERSION/me/accounts" \
    -d "fields=id,name,instagram_business_account,connected_instagram_account" \
    -d "access_token=$LONG_LIVED_TOKEN")

echo "All pages found:"
echo "$PAGES_RESPONSE" | python3 -c "
import sys, json
data = json.load(sys.stdin)
pages = data.get('data', [])
if not pages:
    print('  (none)')
for p in pages:
    print(f\"  Page: {p.get('name')} (id={p.get('id')})\")
    print(f\"    instagram_business_account: {p.get('instagram_business_account')}\")
    print(f\"    connected_instagram_account: {p.get('connected_instagram_account')}\")
"

# Find Instagram account ID — check both link types, optionally filter by page name.
INSTAGRAM_ACCOUNT_ID=$(echo "$PAGES_RESPONSE" | python3 -c "
import sys, json
target = '$TARGET_PAGE_NAME'.lower()
data = json.load(sys.stdin)
pages = data.get('data', [])

# If TARGET_PAGE_NAME is set, prefer matching page; otherwise take first with any IG link.
def ig_id(page):
    return (page.get('instagram_business_account') or page.get('connected_instagram_account') or {}).get('id')

candidates = [p for p in pages if target == '' or target in p.get('name', '').lower()]
if not candidates:
    candidates = pages  # fall back to all pages

for page in candidates:
    aid = ig_id(page)
    if aid:
        print(aid)
        break
")

if [ -z "$INSTAGRAM_ACCOUNT_ID" ]; then
    echo "Error: No Instagram account found linked to any Page (searched for '$TARGET_PAGE_NAME')."
    exit 1
fi

echo "Using Instagram Account ID: $INSTAGRAM_ACCOUNT_ID"

# Fetch account-level insights.
echo "Fetching insights..."

# total_value metrics: aggregated over the period
TOTAL_RESPONSE=$(curl -s -G "https://graph.facebook.com/$API_VERSION/$INSTAGRAM_ACCOUNT_ID/insights" \
    -d "metric=views,profile_views" \
    -d "period=day" \
    -d "metric_type=total_value" \
    -d "access_token=$LONG_LIVED_TOKEN")

# time_series metrics: broken down per day
SERIES_RESPONSE=$(curl -s -G "https://graph.facebook.com/$API_VERSION/$INSTAGRAM_ACCOUNT_ID/insights" \
    -d "metric=reach" \
    -d "period=day" \
    -d "metric_type=time_series" \
    -d "access_token=$LONG_LIVED_TOKEN")

echo "Insights (total_value):"
echo "$TOTAL_RESPONSE" | python3 -c "
import sys, json
data = json.load(sys.stdin)
if 'error' in data:
    print('Error:', json.dumps(data['error'], indent=2))
    sys.exit(1)
for metric in data.get('data', []):
    total = metric.get('total_value', {}).get('value', 'n/a')
    print(f\"  {metric['name']}: {total}\")
"

echo "Insights (time_series — reach by day):"
echo "$SERIES_RESPONSE" | python3 -c "
import sys, json
data = json.load(sys.stdin)
if 'error' in data:
    print('Error:', json.dumps(data['error'], indent=2))
    sys.exit(1)
for metric in data.get('data', []):
    print(f\"  {metric['name']} ({metric['period']})\")
    for v in metric.get('values', []):
        print(f\"    {v['end_time']}: {v['value']}\")
"
