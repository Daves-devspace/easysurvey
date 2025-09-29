📖 Project Documentation: Smart IT Support Bot + CI/CD Pipeline
1. 🤖 IT Support Bot Integration
Files Involved

apps/EasyDocs/bot.py

Implements your Stage 2 prototype IT support bot with:

FAISS for semantic search

Hugging Face T5 paraphraser (local + fallback to HF API)

Summarizer for large answers

Health checks + auto-warning logger for CPU/RAM usage

apps/EasyDocs/management/commands/rebuild_kb.py

CLI command (python manage.py rebuild_kb) to (re)build the knowledge base FAISS index from raw KB JSON files.

Used both locally and in CI/CD.

.coveragerc

Ensures coverage reports include both Django app + bot test suites.

Why It’s Essential

✅ Makes your bot robust, lightweight, and production-ready
✅ Separates bot logic from Django app while keeping them integrated
✅ Provides clear testing hooks (bot test suite vs app test suite)

2. 🐳 Containerization
Files Involved

Dockerfile

Builds a lightweight image:

Installs dependencies

Copies KB index if available

Runs Gunicorn/Django app

Uses BuildKit caching to speed up builds.

entrypoint.sh

Waits for DB/Redis before starting Django

Runs migrations + static collection if enabled

Starts Gunicorn

Why It’s Essential

✅ Guarantees reproducible builds
✅ Works consistently across VPS/Cloud/CI/CD
✅ Self-contained deployment for both bot + Django

3. ⚙️ CI/CD Workflows
Files Involved

.github/workflows/ci.yml

Runs on every push / pull request

Steps:

Cache pip + FAISS index for speed

Run Django tests

Run Bot test suite separately

Collect coverage reports

Upload artifacts (KB index, coverage HTML)

.github/workflows/deploy.yml

Runs on workflow_dispatch (manual trigger)

Builds & pushes Docker image

Runs smoke test (/api/bot_health)

Deploys to branch-specific environments:

master → prod-companyA

main → prod-companyB

Why It’s Essential

✅ Guarantees code quality before deploy
✅ Isolates product environments (Company A vs Company B)
✅ Uses environment protection rules (approvals, restricted secrets)

4. 🌲 Branch → Environment Mapping

You have two active branches, each tied to a different company/product:

master → Company A

Environment: prod-companyA

Registry tag: myapp:master-latest

Secrets: DB/API keys only for Company A

Usually auto-deploys (can skip approvals)

main → Company B

Environment: prod-companyB

Registry tag: myapp:main-latest

Secrets: DB/API keys only for Company B

Requires approval before deployment (environment protection rule)

5. 🚀 What Happens on Push?
Case A: Push to master

CI (ci.yml) runs automatically

Django + bot tests

Coverage report

KB index built if changed

Deploy (deploy.yml)

You manually trigger deployment (workflow_dispatch)

Docker image is built + pushed with tag master-latest

Deploys to prod-companyA

No approval required unless you configure one

➡️ Expectation: Company A’s staging/prod environment updates with the new bot + app.

Case B: Push to main

CI (ci.yml) runs automatically

Same as above (tests, KB, coverage)

Deploy (deploy.yml)

You manually trigger deployment

Docker image is built + pushed with tag main-latest

Deploys to prod-companyB

❌ Workflow pauses until manual approval (environment protection rule)

➡️ Expectation: Company B’s production gets updated only after an authorized person approves.

6. ✅ Why This Setup is Robust

🧩 Separation of Concerns → CI handles testing & artifacts; Deploy handles release.

🚦 Branch-based environments → each company/product fully isolated.

🔐 Environment-scoped secrets → prevents cross-access (Company A can’t see Company B keys).

🛡️ Protection rules → prevents accidental production deploys.

⚡ Optimized caching → pip, FAISS, Docker layers → super fast CI/CD.

📊 Test + coverage enforcement → prevents shipping broken bot logic.