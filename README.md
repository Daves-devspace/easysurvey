### Company A (Master Branch)
![Coverage](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/Daves-devspace/<GIST_ID>/raw/coverage-master.json)


Frontend / bot.js
      |
      v
Django Forwarder (validates X-Bot-Secret)
      |
      v
n8n Webhook Trigger
      |
      v
Secret Validation (Function)
      |
      v
Context Retrieval (Optional)
      |
      v
KB Search (Optional)
      |
      v
LLM Query (Optional)
      |
      v
Fallback Function (if no answer)
      |
      v
Context Update (Optional)
      |
      v
Respond to Webhook -> Django -> Frontend


![alt text](image-2.png)
<!-- 
### Company B (Main Branch)
![Coverage](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/<your-username>/<GIST_ID>/raw/coverage-main.json) -->