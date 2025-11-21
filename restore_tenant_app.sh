#!/usr/bin/env bash
set -euo pipefail

# 0) go to repo root
cd "$(git rev-parse --show-toplevel)"

echo "==> Creating backup branch (main-preserve-backup) and pushing it..."
git checkout main
git branch -f main-preserve-backup
git push -u origin main-preserve-backup

# 1) abort any partial merge (if one is ongoing)
git merge --abort || true

# 2) make temp dir and back up the app+templates you want to preserve
TMPDIR=$(mktemp -d)
echo "==> Backing up tenant_management and templates to $TMPDIR"
mkdir -p "$TMPDIR"/apps "$TMPDIR"/templates
rsync -a apps/tenant_management "$TMPDIR"/apps/ || true

for d in leases meter_readings messages properties tenants units; do
  if [ -d "templates/$d" ]; then
    mkdir -p "$TMPDIR"/templates
    rsync -a "templates/$d" "$TMPDIR"/templates/
  fi
done

# 3) remove compiled caches locally (so they won't interfere)
echo "==> Removing .pyc and __pycache__ from working tree..."
find . -name "*.pyc" -delete || true
find . -type d -name "__pycache__" -prune -exec rm -rf {} + || true

# 4) ensure .gitignore contains sensible entries (append if missing)
echo "==> Updating .gitignore (bytecode, media, logs, staticfiles)..."
cat >> .gitignore <<'EOF'

# Bytecode
__pycache__/
*.pyc

# Django uploads & generated files
media/
logs/
*.pdf
staticfiles/
EOF

# commit .gitignore update if it changed
git add .gitignore
git commit -m "Update .gitignore to ignore bytecode, media and logs" || true

# 5) fetch origin and hard-reset main to origin/master
echo "==> Fetching origin and resetting main to origin/master..."
git fetch origin
git checkout main
git reset --hard origin/master

# 6) restore the backed-up app and templates
echo "==> Restoring preserved app and templates from $TMPDIR ..."
rsync -a "$TMPDIR"/apps/tenant_management apps/ || true
for d in leases meter_readings messages properties tenants units; do
  if [ -d "$TMPDIR"/templates/"$d" ]; then
    mkdir -p templates
    rsync -a "$TMPDIR"/templates/"$d" templates/
  fi
done

# 7) add, commit and push restored content
echo "==> Staging restored files..."
git add apps/tenant_management \
    templates/leases templates/meter_readings templates/messages templates/properties templates/tenants templates/units || true

# commit if there are staged changes
if ! git diff --cached --quiet; then
  git commit -m "Restore tenant_management app + templates after resetting main to master"
else
  echo "No changes to commit (restored content matches origin/master or already present)."
fi

echo "==> Pushing main to origin..."
git push origin main

# 8) cleanup
rm -rf "$TMPDIR"
echo "Done. main now matches origin/master plus the restored tenant_management app/templates."
echo "Backup branch saved as main-preserve-backup (on origin)."
