#!/bin/bash

BASE_URL="https://d49e02a28013.ngrok-free.app"
TEXT="Hello bot, test OpenRouter"
USERNAME="test_user"

echo "1️⃣ Enqueue request..."
RESPONSE=$(curl -s -X POST "$BASE_URL/api/bot/enqueue/" \
  -H "Content-Type: application/json" \
  -d "{\"text\":\"$TEXT\",\"username\":\"$USERNAME\"}")

REQUEST_ID=$(echo "$RESPONSE" | jq -r '.request_id')
POLL_URL=$(echo "$RESPONSE" | jq -r '.poll_url')

echo "Request ID: $REQUEST_ID"
echo "Poll URL: $POLL_URL"

echo ""
echo "2️⃣ Waiting for n8n to process and post the result..."
echo "This may take a few seconds depending on your workflow."

# Poll repeatedly until we get a ready result
while true; do
    RESULT=$(curl -s -X GET "$BASE_URL$POLL_URL")
    STATUS=$(echo "$RESULT" | jq -r '.ok')
    ANSWER=$(echo "$RESULT" | jq -r '.answer')

    if [ "$STATUS" = "true" ] && [ "$ANSWER" != "Workflow was started" ]; then
        echo ""
        echo "✅ Final answer received from n8n:"
        echo "$RESULT" | jq
        break
    else
        echo "Waiting for n8n result..."
        sleep 2
    fi
done
