Threads Graph API Data Retrieval Specification and Architectural Feasibility Report


Endpoint Specifications, Provenance, and cURL Implementations
The following specifications detail the precise cURL commands required to retrieve the requested data. An analysis of their provenance separates examples derived directly from the official documentation from those synthesized via broader developer community research, fulfilling the stakeholder's request for source attribution.
1. User Profile Metadata Retrieval
Retrieves foundational metadata about the target Threads account, necessary for dashboard headers and verification status.
Provenance: Sourced from the User Document and corroborated by official Meta Documentation4.
Method: GET /{threads-user-id}
cURL Example:
Bash
curl -X GET "https://graph.threads.net/v1.0/27469101309343572?fields=id,username,name,threads_profile_picture_url,threads_biography,is_verified&access_token={ACCESS_TOKEN}"

Insight: This endpoint is lightweight and not subject to insight-specific rate limits, making it ideal for routine profile health checks.
2. User Threads (Post) Array Retrieval
Fetches the paginated list of all posts authored by the user, serving as the basis for all subsequent media-level insight queries.
Provenance: Derived from the User Document and validated through Internet Research regarding endpoint structure10.
Method: GET /{threads-user-id}/threads
cURL Example:
Bash
curl -X GET "https://graph.threads.net/v1.0/27469101309343572/threads?fields=id,media_type,text,timestamp,shortcode,poll_attachment&limit=25&access_token={ACCESS_TOKEN}"

Insight: The limit parameter can restrict payload size, optimizing network transfer speeds when polling for daily updates rather than full historical backfills.
3. Account-Level Insights (Aggregated Views & Interactions)
Retrieves overarching performance metrics for the profile over a specific, defined period.
Provenance: Lifted directly from the User Document and verified via Official Meta Documentation8.
Method: GET /{threads-user-id}/threads_insights
cURL Example:
Bash
curl -X GET "https://graph.threads.net/v1.0/27469101309343572/threads_insights?metric=views,likes,replies,reposts,quotes,clicks&since=1712991600&until=1715631600&access_token={ACCESS_TOKEN}"

Insight: Notice the explicit exclusion of the shares metric to prevent composite data overlap, and the explicit inclusion of Unix timestamps ensuring the request complies with the April 13, 2024 boundary.
4. Account-Level Demographics
Retrieves the discrete breakdown of audience demographic categories.
Provenance: Synthesized entirely from Wider Internet Research; official Meta quick-start guides frequently omit the specific formatting rules for the breakdown parameter1.
Method: GET /{threads-user-id}/threads_insights
cURL Example:
Bash
curl -X GET "https://graph.threads.net/v1.0/27469101309343572/threads_insights?metric=follower_demographics&breakdown=age&access_token={ACCESS_TOKEN}"

Insight: The strict omission of since and until parameters is enforced here. Modifying the breakdown value to country, city, or gender in subsequent calls will yield the remaining demographic arrays.
5. Media-Level Insights (Granular Post Performance)
Retrieves the exact performance metrics of a specific piece of content, identified by its unique Media ID.
Provenance: Lifted from the User Document and corroborated by Official Meta Documentation8.
Method: GET /{threads-media-id}/insights
cURL Example:
Bash
curl -X GET "https://graph.threads.net/v1.0/18113985254286218/insights?metric=views,likes,replies,reposts,quotes&access_token={ACCESS_TOKEN}"

Insight: This call forms the core of the Top Content ETL pipeline, requiring execution within a programmatic loop for each Media ID retrieved from the User Threads endpoint.


Meta Graph API Architectural Primer
The Threads API is constructed upon Meta's standardized Graph API framework. This architecture operates via Nodes (discrete objects such as a specific User profile or an individual Post) and Edges (collections of objects or analytical relationships attached to a Node). Programmatic interaction with the API requires querying the graph.threads.net/v1.0 host using a cryptographically signed OAuth 2.0 access token4.
Node and Edge Mapping
The primary nodes and their associated edges relevant to the data requirements are structured as follows:
The User Node (/{threads-user-id}): This represents the overarching Threads account.
Fields: Core metadata such as id, username, name, threads_biography, threads_profile_picture_url, and is_verified4.
Edge - /threads: Returns a cursor-paginated list of Media Nodes (posts) authored by the user9.
Edge - /replies: Returns a paginated list of reply objects made by the user9.
Edge - /threads_insights: Returns account-level analytics. This edge exposes overarching performance metrics such as total profile views, demographic breakdowns, and total current followers4.
The Media Node (/{threads-media-id}): This represents an individual Threads post, encompassing varying formats including text, image, video, or carousels.
Fields: Data points defining the post, including id, media_product_type, media_type (which returns TEXT_POST even for polls and GIFs), media_url, permalink, text, timestamp, shortcode, is_quote_post, has_replies, and poll_attachment8.
Edge - /insights: Returns post-level analytics specifically attributed to that media ID (e.g., views, likes, replies, reposts, quotes, shares)4.
Edge - /conversation: Returns the hierarchy of replies associated with the root post21.
The Message and Thread Objects: The provided documentation references Messages object and Thread object links (v25.0/thread and v25.0/message). It is crucial to clarify that within the Meta developer ecosystem, these specific objects refer to Facebook Messenger and Instagram Direct Messaging architectures, not the public Threads.net microblogging platform. The analysis will therefore strictly utilize the graph.threads.net infrastructure.
Data Requirements Mapping: Fulfilling the Social Team Mandate
The requested metrics have been meticulously mapped against the official Meta Threads API documentation, cross-referenced with wider developer ecosystem implementations, open-source SDKs, and platform behaviors. The following subsections detail the exact methodologies required to fulfill the operational needs, categorized by their alignment with native API capabilities.
Endpoint Specifications, cURL Implementations, and Provenance
The following specifications detail the precise HTTP requests and parameters required to retrieve the requested data. An analysis of their provenance separates examples derived directly from the official Meta documentation from those sourced via broader developer community research or reverse-engineering.

Data Requirement
Target Endpoint
Required Parameters
Provenance
Level of Certainty
Technical Notes
Profile Metadata
GET /{user-id}
fields=id,username,name,threads_biography
User Document & Meta API4
High
Foundation for all subsequent calls.
User Posts (Threads)
GET /{user-id}/threads
fields=id,media_type,text,timestamp,shortcode
Wider Internet Research10
High
Utilizes cursor-based pagination (before, after).
User Replies
GET /{user-id}/replies
Pagination cursors
Official Meta Docs4
High
Independent from main threads endpoint.
Account Insights (Views/Eng)
GET /{user-id}/threads_insights
metric=views,likes,replies,reposts,quotes,clicks
User Document8
High
Supports since and until parameters for time-series.
Gender % Followers
GET /{user-id}/threads_insights
metric=follower_demographics, breakdown=gender
Wider Internet1
High
Fails if <100 followers. Do not pass timestamp parameters.
Total Followers by Day
GET /{user-id}/threads_insights
metric=followers_count
Meta Docs / User Document18
Low (Historical)
Natively returns current count only. Requires daily polling.
Top 5 Locations (City/Country)
GET /{user-id}/threads_insights
metric=follower_demographics, breakdown=city,country
Wider Internet2
High
Returns ISO country codes.
Media/Post Insights
GET /{media-id}/insights
metric=views,likes,replies,reposts,quotes
User Document8
High
Core API functionality.
Polls Data
GET /{user-id}/threads
fields=poll_attachment
Internet Research8
High
Embedded within the TEXT_POST media type.
Link Click Data (CTR)
GET /{user-id}/threads_insights
metric=clicks
User Document5
Low (Post-Level)
Supported at the account level only. Post-level CTR cannot be calculated natively.
Sources Summary
N/A
N/A
Analysis
Zero
Utterly unsupported by the Graph API.
Follows brought by a post
N/A
N/A
Analysis
Zero
Utterly unsupported. Correlational estimation required.

Highlighted cURL Implementations
1. Account-Level Demographics (Wider Internet Provenance) Meta's quick-start documentation frequently omits specific breakdown formatting. Research confirms the following structure is required1:



Bash
curl -X GET "https://graph.threads.net/v1.0/27469101309343572/threads_insights?metric=follower_demographics&breakdown=age&access_token={ACCESS_TOKEN}"


2. Account-Level Insights (User Document Provenance) Retrieves overarching performance metrics for the profile over a specific period. Note the URI encoding (%2C) for comma-separated metrics8.



Bash
curl -X GET "https://graph.threads.net/v1.0/27469101309343572/threads_insights?metric=views%2Clikes%2Creplies%2Creposts%2Cquotes%2Cclicks&since=1712991600&until=1715631600&access_token={ACCESS_TOKEN}"


3. Single Post (Media) Insights (User Document Provenance) Retrieves the granular performance of a specific piece of content based on its unique media ID8.



Bash
curl -X GET "https://graph.threads.net/v1.0/18113985254286218/insights?metric=views,likes,replies,reposts,quotes&access_token={ACCESS_TOKEN}"


Deep Dive: Systems Integration and Deployment Architecture
Integrating the Threads API into the existing organizational Python deployment requires navigating a complex matrix of pagination rules, rate limiting, token lifecycles, and error class structures. The stakeholder documentation specifically references an existing architectural pattern utilizing top-level functions (get_posts, get_page_insights) and distinct error classes (PaginationError, InsightsMetricError).
Integrating with Existing Python Error Classes
Handling Pagination (PaginationError): The /{threads-user-id}/threads endpoint utilizes cursor-based pagination to limit payload sizes and memory consumption. The response payload includes a paging object containing cursors (before and after) and a next URL9. The application logic must execute a while loop within the _paginate_ function that continuously evaluates the presence of the next URL. If an HTTP timeout, a 500 Internal Server Error, or an invalid cursor exception is returned by Meta, the _paginate_ function must catch the raw exception and raise the custom PaginationError back to the get_posts caller. Implementing exponential backoff logic during PaginationError intercepts is highly recommended to circumvent temporary API instability.
Handling Insight Retrieval (InsightsMetricError): Errors fetching metrics are highly frequent in the Meta ecosystem. The InsightsMetricError must be meticulously configured to handle the specific edge cases of the Threads API. The underlying _get function calls fetch_insight_items. This parser must be updated to address two critical Threads anomalies:
If the script attempts to fetch follower_demographics for an account that has dropped below 100 followers, the API will return an empty JSON object ({}) rather than a standard HTTP 400 Bad Request3. The parser must identify empty demographic objects and handle them gracefully, raising an InsightsMetricError with a custom message (e.g., "Demographic threshold unmet") rather than crashing the pipeline due to a KeyError.
The InsightsMetricError must trap queries that violate the April 13, 2024 temporal boundary. Passing a since parameter referencing March 2024 will induce a fatal API response. The underlying _get function must proactively validate Unix timestamps against the 1712991600 constant before generating the HTTP request.
Constants and Mapping
The current deployment utilizes configuration files (Facebook/api.py) containing lists of associated metrics, such as PAGE_DAY_METRIC, PAGE_LIFETIME_METRICS, and POST_METRICS_REACTION_FIELDS. To adapt this to Threads, equivalent constants must be instantiated:
THREADS_ACCOUNT_DAY_METRICS = ["views", "likes", "replies", "reposts", "quotes", "clicks"]
THREADS_ACCOUNT_LIFETIME_METRICS = ["followers_count", "follower_demographics"]
THREADS_MEDIA_METRICS = ["views", "likes", "replies", "reposts", "quotes"]

Additional information on threads_insights

That's all four payloads in full above — age and gender in the first block, country and city in the second. The only formatting liberty I took is putting each results entry on one line so the 45-bucket lists stay readable; the values are verbatim, and the raw responses are still at /tmp/th.{age,gender,country,city} if you want them untouched.

Two things visible now that the full lists are printed:

Neither country nor city is ordered. Country starts DE, BD, RU, TW, HK… with GB (91% of followers) sitting 16th; city starts Salford, Manchester, London…. Any ranking has to be done client-side — don't rely on API order.
Both lists are exactly 45 entries, which alongside the floors noted earlier (lowest country NZ at 40, lowest city Croydon at 255) confirms this is a cap rather than the true tail. City in particular is UK-only apart from Hong Kong, Hong Kong, so the 25,862 city total is a heavily truncated view of the 86,110 followers.


age Breakdown:
[https://graph.threads.net/v1.0/me/threads_insights?metric=follower_demographics&breakdown=age&access_token=YOUR_ACCESS_TOKEN](https://graph.threads.net/v1.0/me/threads_insights?metric=follower_demographics&breakdown=age&access_token=YOUR_ACCESS_TOKEN)

[cite: 1, 3]

Gender Breakdown:
[https://graph.threads.net/v1.0/me/threads_insights?metric=follower_demographics&breakdown=gender&access_token=YOUR_ACCESS_TOKEN](https://graph.threads.net/v1.0/me/threads_insights?metric=follower_demographics&breakdown=gender&access_token=YOUR_ACCESS_TOKEN)

[cite: 1]

Country Breakdown:
[https://graph.threads.net/v1.0/me/threads_insights?metric=follower_demographics&breakdown=country&access_token=YOUR_ACCESS_TOKEN](https://graph.threads.net/v1.0/me/threads_insights?metric=follower_demographics&breakdown=country&access_token=YOUR_ACCESS_TOKEN)

[cite: 1]

City Breakdown:
[https://graph.threads.net/v1.0/me/threads_insights?metric=follower_demographics&breakdown=city&access_token=YOUR_ACCESS_TOKEN](https://graph.threads.net/v1.0/me/threads_insights?metric=follower_demographics&breakdown=city&access_token=YOUR_ACCESS_TOKEN)






Outputs:
######## breakdown=age ########
{
    "data": [
        {
            "id": "17841465380896544/insights/follower_demographics/day",
            "name": "follower_demographics",
            "period": "day",
            "title": "follower_demographics",
            "description": "The demographic characteristics of followers, including countries, cities and gender distribution.",
            "total_value": {
                "breakdowns": [
                    {
                        "dimension_keys": [
                            "age"
                        ],
                        "results": [
                            {
                                "dimension_values": [
                                    "13-17"
                                ],
                                "value": 1329
                            },
                            {
                                "dimension_values": [
                                    "18-24"
                                ],
                                "value": 7434
                            },
                            {
                                "dimension_values": [
                                    "25-34"
                                ],
                                "value": 19220
                            },
                            {
                                "dimension_values": [
                                    "35-44"
                                ],
                                "value": 20282
                            },
                            {
                                "dimension_values": [
                                    "45-54"
                                ],
                                "value": 16550
                            },
                            {
                                "dimension_values": [
                                    "55-64"
                                ],
                                "value": 12871
                            },
                            {
                                "dimension_values": [
                                    "65+"
                                ],
                                "value": 8424
                            }
                        ]
                    }
                ]
            }
        }
    ],
    "paging": {
        "previous": "/v1.0/user/27469101309343572/threads_insights?since=1786976091&until=1787062491&metric=follower_demographics&breakdown=age",
        "next": "/v1.0/user/27469101309343572/threads_insights?since=1787148893&until=1787235293&metric=follower_demographics&breakdown=age"
    }
}

######## breakdown=gender ########
{
    "data": [
        {
            "id": "17841465380896544/insights/follower_demographics/day",
            "name": "follower_demographics",
            "period": "day",
            "title": "follower_demographics",
            "description": "The demographic characteristics of followers, including countries, cities and gender distribution.",
            "total_value": {
                "breakdowns": [
                    {
                        "dimension_keys": [
                            "gender"
                        ],
                        "results": [
                            {
                                "dimension_values": [
                                    "F"
                                ],
                                "value": 38223
                            },
                            {
                                "dimension_values": [
                                    "M"
                                ],
                                "value": 35592
                            },
                            {
                                "dimension_values": [
                                    "U"
                                ],
                                "value": 12295
                            }
                        ]
                    }
                ]
            }
        }
    ],
    "paging": {
        "previous": "/v1.0/user/27469101309343572/threads_insights?since=1786976091&until=1787062491&metric=follower_demographics&breakdown=gender",
        "next": "/v1.0/user/27469101309343572/threads_insights?since=1787148893&until=1787235293&metric=follower_demographics&breakdown=gender"
    }
}

######## breakdown=country ########
{
  "data": [
    {
      "id": "17841465380896544/insights/follower_demographics/day",
      "name": "follower_demographics",
      "period": "day",
      "title": "follower_demographics",
      "description": "The demographic characteristics of followers, including countries, cities and gender distribution.",
      "total_value": {
        "breakdowns": [
          {
            "dimension_keys": ["country"],
            "results": [
              {"dimension_values": ["DE"], "value": 154},
              {"dimension_values": ["BD"], "value": 105},
              {"dimension_values": ["RU"], "value": 57},
              {"dimension_values": ["TW"], "value": 204},
              {"dimension_values": ["HK"], "value": 423},
              {"dimension_values": ["PT"], "value": 103},
              {"dimension_values": ["BG"], "value": 59},
              {"dimension_values": ["JP"], "value": 101},
              {"dimension_values": ["NZ"], "value": 40},
              {"dimension_values": ["UA"], "value": 99},
              {"dimension_values": ["FR"], "value": 109},
              {"dimension_values": ["SA"], "value": 58},
              {"dimension_values": ["BR"], "value": 180},
              {"dimension_values": ["KE"], "value": 96},
              {"dimension_values": ["ID"], "value": 139},
              {"dimension_values": ["GB"], "value": 77376},
              {"dimension_values": ["IE"], "value": 169},
              {"dimension_values": ["CA"], "value": 199},
              {"dimension_values": ["US"], "value": 1031},
              {"dimension_values": ["GH"], "value": 127},
              {"dimension_values": ["EG"], "value": 100},
              {"dimension_values": ["AE"], "value": 121},
              {"dimension_values": ["IN"], "value": 647},
              {"dimension_values": ["KR"], "value": 71},
              {"dimension_values": ["ZA"], "value": 92},
              {"dimension_values": ["IQ"], "value": 42},
              {"dimension_values": ["GR"], "value": 63},
              {"dimension_values": ["MX"], "value": 63},
              {"dimension_values": ["IT"], "value": 164},
              {"dimension_values": ["CN"], "value": 183},
              {"dimension_values": ["MY"], "value": 217},
              {"dimension_values": ["ES"], "value": 202},
              {"dimension_values": ["KZ"], "value": 46},
              {"dimension_values": ["TH"], "value": 75},
              {"dimension_values": ["AU"], "value": 194},
              {"dimension_values": ["CY"], "value": 47},
              {"dimension_values": ["VN"], "value": 98},
              {"dimension_values": ["PH"], "value": 142},
              {"dimension_values": ["NG"], "value": 463},
              {"dimension_values": ["PK"], "value": 320},
              {"dimension_values": ["PL"], "value": 71},
              {"dimension_values": ["ZW"], "value": 46},
              {"dimension_values": ["RO"], "value": 178},
              {"dimension_values": ["NL"], "value": 55},
              {"dimension_values": ["TR"], "value": 91}
            ]
          }
        ]
      }
    }
  ],
  "paging": {
    "previous": "/v1.0/user/27469101309343572/threads_insights?since=1786976092&until=1787062492&metric=follower_demographics&breakdown=country",
    "next": "/v1.0/user/27469101309343572/threads_insights?since=1787148894&until=1787235294&metric=follower_demographics&breakdown=country"
  }
}

######## breakdown=city ########
{
  "data": [
    {
      "id": "17841465380896544/insights/follower_demographics/day",
      "name": "follower_demographics",
      "period": "day",
      "title": "follower_demographics",
      "description": "The demographic characteristics of followers, including countries, cities and gender distribution.",
      "total_value": {
        "breakdowns": [
          {
            "dimension_keys": ["city"],
            "results": [
              {"dimension_values": ["Salford, England"], "value": 374},
              {"dimension_values": ["Manchester, England"], "value": 821},
              {"dimension_values": ["London, England"], "value": 7949},
              {"dimension_values": ["Luton, England"], "value": 303},
              {"dimension_values": ["Leicester, England"], "value": 468},
              {"dimension_values": ["Liverpool, England"], "value": 679},
              {"dimension_values": ["Hong Kong, Hong Kong"], "value": 423},
              {"dimension_values": ["Wembley, England"], "value": 245},
              {"dimension_values": ["Swindon, England"], "value": 237},
              {"dimension_values": ["Plymouth, England"], "value": 276},
              {"dimension_values": ["Warrington, England"], "value": 278},
              {"dimension_values": ["Newcastle upon Tyne, England"], "value": 378},
              {"dimension_values": ["Telford, England"], "value": 232},
              {"dimension_values": ["Reading, England"], "value": 269},
              {"dimension_values": ["Barnsley, England"], "value": 238},
              {"dimension_values": ["Bristol, England"], "value": 553},
              {"dimension_values": ["Kingston upon Hull, England"], "value": 333},
              {"dimension_values": ["Oldham, England"], "value": 239},
              {"dimension_values": ["Aberdeen, Scotland"], "value": 241},
              {"dimension_values": ["Nottingham, England"], "value": 411},
              {"dimension_values": ["Coventry, England"], "value": 493},
              {"dimension_values": ["Ilford, England"], "value": 287},
              {"dimension_values": ["Harrow, England"], "value": 274},
              {"dimension_values": ["Glasgow, Scotland"], "value": 715},
              {"dimension_values": ["Portsmouth, England"], "value": 271},
              {"dimension_values": ["Preston, England"], "value": 267},
              {"dimension_values": ["Leeds, England"], "value": 714},
              {"dimension_values": ["Edinburgh, Scotland"], "value": 587},
              {"dimension_values": ["Stoke-on-Trent, England"], "value": 335},
              {"dimension_values": ["Sheffield, England"], "value": 665},
              {"dimension_values": ["Birmingham, England"], "value": 1396},
              {"dimension_values": ["Walsall, England"], "value": 314},
              {"dimension_values": ["Wolverhampton, England"], "value": 331},
              {"dimension_values": ["Stockport, England"], "value": 331},
              {"dimension_values": ["Northampton, England"], "value": 338},
              {"dimension_values": ["Cardiff, Wales"], "value": 463},
              {"dimension_values": ["Derby, England"], "value": 310},
              {"dimension_values": ["Belfast, Northern Ireland"], "value": 313},
              {"dimension_values": ["Southampton, England"], "value": 351},
              {"dimension_values": ["Bradford, England"], "value": 584},
              {"dimension_values": ["Bournemouth, England"], "value": 246},
              {"dimension_values": ["Milton Keynes, England"], "value": 373},
              {"dimension_values": ["Brighton and Hove, England"], "value": 316},
              {"dimension_values": ["Bolton, England"], "value": 386},
              {"dimension_values": ["Croydon, England"], "value": 255}
            ]
          }
        ]
      }
    }
  ],
  "paging": {
    "previous": "/v1.0/user/27469101309343572/threads_insights?since=1786976092&until=1787062492&metric=follower_demographics&breakdown=city",
    "next": "/v1.0/user/27469101309343572/threads_insights?since=1787148894&until=1787235294&metric=follower_demographics&breakdown=city"
  }
}
Shell cwd was reset to /Users/ORoberts1/git/govuk-social-data-2





