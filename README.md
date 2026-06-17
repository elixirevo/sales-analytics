## Daily POS Sales Analytics Pipeline

Toss Place POS 주문/결제 데이터를 수집해 DB에 저장하고, pandas 분석 CSV를 생성한 뒤 Google Drive 또는 로컬 업로드 디렉터리로 전송하는 배치 애플리케이션입니다.

기본 설정은 실계정 없이 바로 실행 가능한 `mock` 수집기와 `local` 업로드 모드입니다.

### 로컬 실행

```bash
uv run sales-analytics run --business-date 2026-06-17
```

생성 결과:

- SQLite DB: `data/sales_analytics.db`
- CSV 리포트: `reports/merchant_1/YYYY-MM-DD/`
- 로컬 업로드 사본: `uploads/merchant_1_Demo_Store/YYYY/MM/YYYY-MM-DD/`

### Docker 이미지 빌드 및 실행

```bash
docker build -t sales-analytics:latest .
docker run --rm \
  -v "$PWD/data:/app/data" \
  -v "$PWD/reports:/app/reports" \
  -v "$PWD/uploads:/app/uploads" \
  sales-analytics:latest
```

또는:

```bash
docker compose run --rm sales-analytics run --business-date 2026-06-17
```

### 주요 명령

```bash
sales-analytics init-db
sales-analytics serve
sales-analytics run --business-date 2026-06-17
sales-analytics backfill --from-date 2026-06-01 --to-date 2026-06-17
```

### 서버형 스케줄러

컨테이너 기본 명령은 `serve`입니다. 프로세스가 계속 실행되면서 매장별 `business_close_time + SCHEDULER_CLOSE_DELAY_MINUTES`가 지난 영업일을 찾아 한 번씩 배치를 실행합니다.

```bash
uv run sales-analytics serve
```

Docker:

```bash
docker run --name sales-analytics-server --env-file .env \
  -v "$PWD/data:/app/data" \
  -v "$PWD/reports:/app/reports" \
  -v "$PWD/uploads:/app/uploads" \
  sales-analytics:latest
```

동작 확인용으로 한 번만 스케줄러 tick을 실행할 수 있습니다.

```bash
uv run sales-analytics serve --once
```

스케줄 설정:

- `SCHEDULER_POLL_SECONDS`: due 작업 확인 주기
- `SCHEDULER_CLOSE_DELAY_MINUTES`: 영업 종료 후 지연 실행 시간
- `SCHEDULER_LOOKBACK_DAYS`: 서버 재시작 시 놓친 과거 영업일 확인 범위

이미 `SUCCESS`인 매장/영업일은 서버 모드에서 다시 실행하지 않습니다. 재처리가 필요하면 `run --business-date` 또는 `backfill`을 사용합니다.

### 환경 변수

`.env.example`을 기준으로 설정합니다.

- `DATABASE_URL`: 기본 `sqlite:///data/sales_analytics.db`, PostgreSQL 예시는 `postgresql+psycopg://user:pass@host:5432/db`
- `TOSS_CLIENT_MODE`: `mock` 또는 `http`
- `UPLOAD_MODE`: `local`, `google`, `google_oauth`, `google_adc`, `disabled`
- `MERCHANTS_JSON`: 매장 설정 JSON 배열
- `GOOGLE_APPLICATION_CREDENTIALS`: `UPLOAD_MODE=google`일 때 서비스 계정 JSON 경로
- `GOOGLE_OAUTH_CLIENT_SECRETS_FILE`: `auth-google` 최초 로그인에 사용할 OAuth 클라이언트 JSON 경로
- `GOOGLE_OAUTH_TOKEN_FILE`: 개인 Google 계정 OAuth 토큰 저장 경로
- `SCHEDULER_POLL_SECONDS`: 서버 모드 polling 주기
- `SCHEDULER_CLOSE_DELAY_MINUTES`: 영업 종료 후 배치 실행 지연 시간
- `SCHEDULER_LOOKBACK_DAYS`: 놓친 배치 확인 범위

실제 Toss/Google 운영 credential은 코드나 Git에 두지 말고 Secret Manager 또는 런타임 secret으로 주입해야 합니다.

### 개인 Google 계정 OAuth 업로드

서비스 계정 대신 개인 Google 계정으로 업로드하려면 `google_oauth` 모드를 사용합니다.

1. Google Cloud Console에서 `Google Drive API`를 활성화합니다.
2. OAuth 동의 화면을 설정합니다. 테스트 앱이면 본인 Google 계정을 테스트 사용자에 추가합니다.
3. OAuth 클라이언트 ID를 `Desktop app` 유형으로 만들고 JSON을 다운로드합니다.
4. 다운로드한 파일을 예를 들어 `secrets/google-oauth-client.json`에 둡니다.
5. 로컬에서 최초 1회 인증을 실행해 refresh token을 생성합니다.

```bash
GOOGLE_OAUTH_CLIENT_SECRETS_FILE=secrets/google-oauth-client.json \
GOOGLE_OAUTH_TOKEN_FILE=data/google_oauth_token.json \
uv run sales-analytics auth-google
```

또는 `.env`를 수정하지 않고 직접 경로를 넘길 수 있습니다.

```bash
uv run sales-analytics auth-google \
  --client-secrets-file secrets/google-oauth-client.json \
  --token-file data/google_oauth_token.json
```

브라우저에서 Google 계정 로그인을 완료하면 `data/google_oauth_token.json`이 생성됩니다.

이후 `.env`는 이렇게 설정합니다.

```env
UPLOAD_MODE=google_oauth
GOOGLE_OAUTH_CLIENT_SECRETS_FILE=secrets/google-oauth-client.json
GOOGLE_OAUTH_TOKEN_FILE=data/google_oauth_token.json
GOOGLE_OAUTH_AUTO_AUTH=true
```

이 상태에서 로컬 실행 시 토큰이 없으면 브라우저 로그인 화면이 자동으로 열립니다.

```bash
uv run sales-analytics run
```

Docker 실행 시에는 로컬에서 생성된 `data/google_oauth_token.json`을 `data/` 볼륨으로 마운트해 재사용하는 방식을 권장합니다.

```bash
docker run --rm --env-file .env \
  -v "$PWD/data:/app/data" \
  -v "$PWD/reports:/app/reports" \
  -v "$PWD/uploads:/app/uploads" \
  sales-analytics:latest run
```

`MERCHANTS_JSON`의 `drive_folder_id`에 업로드 대상 Drive 폴더 ID를 넣으면 해당 폴더로 업로드합니다. 비워두면 내 Drive 기본 위치로 생성합니다.

Google Drive 업로드 경로는 다음 구조로 자동 생성됩니다.

```text
{drive_folder_id 또는 My Drive root}/
  merchant_{merchant_id}_{merchant_name}/
    YYYY/
      MM/
        YYYY-MM-DD/
          CSV files...
```

### 더 간단한 개인 계정 로그인: gcloud ADC

OAuth 클라이언트 JSON을 만들기 싫다면 Google Cloud CLI의 Application Default Credentials를 사용할 수 있습니다.

```bash
gcloud auth application-default login \
  --scopes=https://www.googleapis.com/auth/drive.file
```

브라우저에서 Google 계정 로그인을 완료한 뒤 `.env`를 이렇게 설정합니다.

```env
UPLOAD_MODE=google_adc
```

로컬 실행:

```bash
uv run sales-analytics run
```

Docker에서 쓰려면 gcloud가 만든 ADC 파일을 컨테이너에 마운트합니다.

```bash
docker run --rm --env-file .env \
  -v "$PWD/data:/app/data" \
  -v "$PWD/reports:/app/reports" \
  -v "$PWD/uploads:/app/uploads" \
  -v "$HOME/.config/gcloud:/root/.config/gcloud:ro" \
  sales-analytics:latest run
```
