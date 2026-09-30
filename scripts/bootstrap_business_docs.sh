#!/usr/bin/env bash
# Provision a new private workspace only. This is not the synchronization engine.
set -euo pipefail
set +x
umask 077
export GH_HOST=github.com
export GH_PROMPT_DISABLED=1

repo='maksimp6/Chat-business-docs'
owner='maksimp6'
mode="${1:---check}"
if [[ $# -gt 1 || ( "$mode" != '--check' && "$mode" != '--create' ) ]]; then
  printf 'Usage: bash scripts/bootstrap_business_docs.sh [--check|--create]\n' >&2
  exit 2
fi
command -v gh >/dev/null || { printf 'BLOCKED: gh unavailable\n' >&2; exit 2; }
command -v git >/dev/null || { printf 'BLOCKED: git unavailable\n' >&2; exit 2; }

# Never print credential-bearing diagnostics or alter global authentication.
if ! identity=$(gh api --hostname github.com user --jq '[.login, (.id | tostring)] | @tsv' 2>/dev/null); then
  printf 'BLOCKED: authenticated GitHub identity unavailable\n' >&2
  exit 2
fi
IFS=$'\t' read -r login user_id <<< "$identity"
if [[ "$login" != "$owner" || ! "$user_id" =~ ^[0-9]+$ ]]; then
  printf 'BLOCKED: unexpected GitHub owner identity\n' >&2
  exit 2
fi

metadata_query='[.private, .fork, .default_branch] | @tsv'
work=$(mktemp -d "${TMPDIR:-/tmp}/business-docs-bootstrap.XXXXXXXX")
# Preserve the directory for diagnosis. No deletion/revert/force operations.
if metadata=$(gh api --hostname github.com "repos/$repo" --jq "$metadata_query" 2>"$work/metadata-error.txt"); then
  if [[ "$metadata" != $'true\tfalse\tmaster' ]]; then
    printf 'BLOCKED: existing target must be PRIVATE, non-fork, master; no changes made\n' >&2
    exit 2
  fi
  printf 'EXISTS_PRIVATE: %s; unchanged; mirror configuration NOT verified\n' "$repo"
  exit 0
fi
if ! grep -q '(HTTP 404)' "$work/metadata-error.txt"; then
  printf 'BLOCKED: cannot inspect repository; no create attempted\n' >&2
  exit 2
fi
if [[ "$mode" == '--check' ]]; then
  printf 'BLOCKED: target absent or inaccessible; provisioning NOT verified\n' >&2
  exit 2
fi

# Prepare and sign safe scaffold BEFORE creating any remote resource.
git init --quiet --initial-branch=master "$work/repository"
cd "$work/repository"
git config user.name "$login"
git config user.email "$user_id+$login@users.noreply.github.com"
git config commit.gpgsign true
mkdir -p public private .mirror
cat > README.md <<'MD'
# Alice Pro: закрытые бизнес-документы

Связанный публичный проект: https://github.com/maksimp6/Chat

- `private/`: закрытые документы, классификация по умолчанию.
- `public/`: кандидаты на публикацию, не автоматическое разрешение раскрытия.
- `.mirror/`: закрытое состояние и разрешения на публикацию.

Автоматическое зеркало НЕ настроено. Не импортировать private Git-историю в Chat.
Экспорт конкретного содержимого требует предварительного разрешения владельца.
MD
cat > private/README.md <<'MD'
# Закрытые материалы

Содержимое и названия файлов этого раздела не публикуются в Chat, PR, Issues,
логах или общедоступных артефактах. Секреты доступа храните в secret manager.
MD
cat > public/README.md <<'MD'
# Публичные документы бизнеса

Этот раздел предназначен только для материалов, разрешённых к открытой публикации.

Не размещайте здесь договоры с персональными данными, банковские документы,
внутреннюю переписку, ключи доступа и другие закрытые материалы.

Публикация новой версии из закрытого хранилища требует предварительного разрешения
владельца на её точное содержимое. Проверка публичного PR не заменяет это разрешение.
MD
cat > .mirror/state.json <<'JSON'
{
  "schema_version": 1,
  "enabled": false,
  "status": "not_configured",
  "baseline": null,
  "publication_approvals": []
}
JSON
git add -- README.md private/README.md public/README.md .mirror/state.json
if ! git commit --quiet -m 'docs: initialize private business workspace' >"$work/commit-output.txt" 2>&1; then
  printf 'BLOCKED: signed scaffold commit failed; remote NOT created\n' >&2
  exit 2
fi
if ! gh repo create "$repo" --private --description 'Private business documents for Alice Pro' >"$work/create-output.txt" 2>&1; then
  printf 'BLOCKED: private repository creation failed; no push attempted\n' >&2
  exit 2
fi
# Re-check privacy before sending even the non-confidential scaffold.
if ! privacy=$(gh api --hostname github.com "repos/$repo" --jq '[.private, .fork] | @tsv' 2>/dev/null) || [[ "$privacy" != $'true\tfalse' ]]; then
  printf 'BLOCKED: created target privacy unverified; no push attempted\n' >&2
  exit 2
fi
git remote add origin "https://github.com/$repo.git"
if ! git -c credential.helper= -c 'credential.helper=!gh auth git-credential' push origin master >"$work/push-output.txt" 2>&1; then
  printf 'BLOCKED: initial push failed; private repository may exist empty; do not delete/recreate\n' >&2
  exit 2
fi
if ! metadata=$(gh api --hostname github.com "repos/$repo" --jq "$metadata_query" 2>/dev/null) || [[ "$metadata" != $'true\tfalse\tmaster' ]]; then
  printf 'BLOCKED: final private/master verification failed\n' >&2
  exit 2
fi
printf 'PROVISIONED: %s PRIVATE master; automatic mirror NOT configured\n' "$repo"
