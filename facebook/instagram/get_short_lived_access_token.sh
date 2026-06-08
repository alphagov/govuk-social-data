#!/bin/bash

APP_ID="your_app_id"
APP_SECRET="your_app_secret"

# Requesting an App Access Token
TOKEN_RESPONSE=$(curl -s -X GET "https://graph.facebook.com/oauth/access_token?client_id=$APP_ID&client_secret=$APP_SECRET&grant_type=client_credentials")

echo  $TOKEN_RESPONSE