# PRD: POS 매출 데이터 수집·분석·Google Drive 업로드 자동화

문서 버전: v0.1  
작성일: 2026-06-17  
대상 시스템: Toss Place POS 매출 데이터 자동 수집 및 분석 파이프라인  
기술 스택 기준: Python, pandas, PostgreSQL, Toss Place Open API, Google Drive API

---

## 1. 제품 개요

### 1.1 제품명

**Daily POS Sales Analytics Pipeline**

### 1.2 목적

매일 영업 종료 후 Toss Place POS 매출 데이터를 자동으로 수집하여 DB에 저장하고, pandas 기반으로 매출 증가 및 경영 관리 관점의 분석 지표를 생성한 뒤, 분석 결과와 원천 데이터를 CSV 파일로 만들어 Google Drive에 자동 업로드한다.

### 1.3 배경

매장 운영자는 매일 발생하는 POS 주문·결제 데이터를 바탕으로 매출 추세, 상품별 성과, 시간대별 피크, 결제수단 구성, 취소/환불, 할인 효과 등을 빠르게 파악해야 한다.

Toss Place Open API는 주문, 결제, 상품 정보에 접근할 수 있으므로 매출 분석용 데이터 파이프라인의 원천으로 사용할 수 있다.

---

## 2. 목표와 비목표

### 2.1 목표

1. 매일 영업 종료 후 해당 영업일의 POS 주문·결제 데이터를 자동 수집한다.
2. 수집한 원천 JSON과 정규화된 테이블 데이터를 DB에 저장한다.
3. pandas로 매출 증가 및 경영 관리에 필요한 분석 데이터를 생성한다.
4. 매일 원천/분석 CSV 파일을 생성한다.
5. 생성된 CSV 파일을 Google Drive 지정 폴더에 업로드한다.
6. 중복 수집, API 실패, 업로드 실패, 취소 주문 반영 등 운영 리스크를 제어한다.

### 2.2 비목표

MVP에서는 아래 기능을 제외한다.

- 실시간 대시보드
- POS 화면 내 UI 확장
- 자동 마케팅 메시지 발송
- 회계 시스템 전표 자동 생성
- ML 기반 수요 예측
- 자동 발주 추천

---

## 3. 사용자 및 이해관계자

| 구분 | 설명 |
|---|---|
| 매장 대표/점주 | 일별 매출, 상품 성과, 취소/할인, 피크 시간대를 확인 |
| 매니저 | 인력 배치, 품절/인기 상품, 운영 효율 지표 확인 |
| 재무/회계 담당자 | 결제수단별 매출, 세액, 공급가액, 취소 내역 확인 |
| 개발/운영자 | API 연동, 배치 성공 여부, 데이터 품질, 업로드 상태 관리 |

---

## 4. 핵심 사용자 시나리오

### 4.1 일별 자동 매출 저장

매장 영업 종료 후 시스템이 해당 영업일 기준 조회 기간을 계산하고 Toss Place Open API에서 주문 목록을 가져온다.

기본적으로 완료 주문과 취소 주문을 함께 수집해 매출과 취소/환불을 모두 반영한다.

### 4.2 일별 분석 생성

수집된 주문, 결제, 상품, 할인 데이터를 pandas DataFrame으로 변환하여 다음과 같은 분석 데이터를 생성한다.

- 일별 매출 요약
- 상품별 판매량 및 매출
- 카테고리별 매출
- 시간대별 매출
- 객단가
- 할인율
- 취소율
- 결제수단별 매출
- 경영 관리용 이상 징후

### 4.3 CSV 자동 업로드

분석 결과 CSV와 필요 시 원천 주문/결제 CSV를 생성하여 Google Drive 지정 폴더에 업로드한다.

---

## 5. 기능 요구사항

## 5.1 Toss Place API 연동

### FR-API-001. 매장 인증 정보 관리

시스템은 Toss Place 개발자 센터에서 발급받은 API key pair를 안전하게 저장하고 API 요청 헤더에 포함해야 한다.

| 항목 | 내용 |
|---|---|
| Secret 저장 | `.env` 직접 저장 금지. AWS Secrets Manager, GCP Secret Manager, Doppler, Vault 등 사용 |
| 매장별 키 | 다중 매장 지원을 고려해 `merchant_id` 단위로 credential 매핑 |
| 키 회전 | 키 교체 시 무중단 반영 가능해야 함 |
| 접근 권한 | 운영자와 배치 서버만 읽기 가능 |

### FR-API-002. 매장 등록 및 설치 상태 관리

시스템은 매장별 기본 정보를 관리해야 한다.

| 필드 | 설명 |
|---|---|
| `merchant_id` | Toss Place 매장 ID |
| `merchant_name` | 매장명 |
| `business_number` | 사업자등록번호 |
| `timezone` | 기본값 `Asia/Seoul` |
| `business_open_time` | 영업 시작 시각 |
| `business_close_time` | 영업 종료 시각 |
| `drive_folder_id` | CSV 업로드 대상 Google Drive 폴더 |
| `is_active` | 배치 대상 여부 |

### FR-API-003. 일별 주문 데이터 수집

시스템은 매일 영업 종료 후 `from`, `to` 조회 범위를 계산하여 주문 목록 API를 호출한다.

#### 기본 조회 조건

| 파라미터 | 기본값 |
|---|---|
| `from` | 영업일 시작 시각 |
| `to` | 영업일 종료 시각 + 보정 버퍼 |
| `orderStates` | `COMPLETED`, `CANCELLED` |
| `page` | 1부터 증가 |
| `size` | API 허용 최대값 이내 |
| `sortOrder` | `ASC` 권장 |

#### 수집 정책

1. 페이지네이션을 끝까지 순회한다.
2. API 응답 원문 JSON을 먼저 저장한다.
3. 정규화 테이블에 upsert한다.
4. 동일 주문 ID 재수집 시 최신 변경 시각 기준으로 갱신한다.
5. `CANCELLED` 주문은 삭제하지 않고 상태값으로 보존한다.

### FR-API-004. 결제 데이터 수집 및 정합성 검증

주문 데이터와 결제 데이터를 함께 저장하고, 주문 총액과 결제 합계의 정합성을 검증한다.

| 항목 | 내용 |
|---|---|
| 결제 상태 | 승인, 취소 구분 |
| 결제수단 | 현금, 카드, 계좌이체, 간편결제, 외부 결제수단 등 구분 |
| 금액 | 결제금액, 공급가액, 세액, 면세금액 저장 |
| 취소 | 취소 시각과 취소 상태 반영 |
| 정합성 | 주문 총액과 결제 합계 비교 |

### FR-API-005. API 호출량 제한 대응

시스템은 API 호출 제한과 일시적 장애에 대응해야 한다.

1. HTTP 429 발생 시 일정 시간 대기 후 재시도한다.
2. 매장별 rate limiter를 둔다.
3. 재시도는 exponential backoff + jitter를 적용한다.
4. 401 인증 오류는 재시도하지 않고 즉시 실패 처리한다.
5. 5xx 오류는 최대 N회 재시도 후 실패 큐에 적재한다.

---

## 5.2 데이터 저장

### FR-DB-001. Raw 데이터 저장

API 응답 원문은 분석 로직 변경, 재처리, 감사 추적을 위해 그대로 저장한다.

#### 테이블: `raw_pos_api_responses`

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `id` | UUID | 내부 ID |
| `merchant_id` | BIGINT | 매장 ID |
| `business_date` | DATE | 영업일 |
| `endpoint` | TEXT | 호출 API |
| `request_params` | JSONB | 요청 파라미터 |
| `response_body` | JSONB | 원문 응답 |
| `http_status` | INT | HTTP 상태 |
| `x_toss_event_id` | TEXT | Toss 요청 추적 ID |
| `created_at` | TIMESTAMP | 저장 시각 |

### FR-DB-002. 주문 정규화 저장

#### 테이블: `orders`

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `order_id` | TEXT PK | Toss 주문 ID |
| `merchant_id` | BIGINT | 매장 ID |
| `business_date` | DATE | 영업일 |
| `source` | TEXT | POS, KIOSK 등 주문 채널 |
| `order_state` | TEXT | 완료, 취소 등 주문 상태 |
| `order_number` | TEXT | 매장 주문번호 |
| `created_at` | TIMESTAMP | 생성 시각 |
| `completed_at` | TIMESTAMP | 완료 시각 |
| `cancelled_at` | TIMESTAMP | 취소 시각 |
| `list_price` | BIGINT | 원금액 |
| `discount_amount` | BIGINT | 할인금액 |
| `tax_amount` | BIGINT | 세액 |
| `supply_amount` | BIGINT | 공급가액 |
| `tax_exempt_amount` | BIGINT | 면세금액 |
| `total_amount` | BIGINT | 최종금액 |
| `updated_at` | TIMESTAMP | API 변경 시각 |
| `ingested_at` | TIMESTAMP | 수집 시각 |

### FR-DB-003. 주문 항목 저장

#### 테이블: `order_line_items`

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `id` | UUID PK | 내부 ID |
| `order_id` | TEXT FK | 주문 ID |
| `merchant_id` | BIGINT | 매장 ID |
| `business_date` | DATE | 영업일 |
| `item_title` | TEXT | 상품명 |
| `item_code` | TEXT | 상품 코드 |
| `category_title` | TEXT | 카테고리 |
| `dining_option` | TEXT | 매장식사/포장/배달/픽업 |
| `quantity` | BIGINT | 수량 |
| `unit_price` | BIGINT | 단가 |
| `option_amount` | BIGINT | 옵션 추가 금액 |
| `line_discount_amount` | BIGINT | 항목 할인 |
| `line_total_amount` | BIGINT | 항목 총액 |

### FR-DB-004. 결제 저장

#### 테이블: `payments`

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `payment_id` | TEXT PK | 결제 ID |
| `order_id` | TEXT FK | 주문 ID |
| `merchant_id` | BIGINT | 매장 ID |
| `business_date` | DATE | 영업일 |
| `state` | TEXT | 승인, 취소 등 결제 상태 |
| `source_type` | TEXT | 현금, 카드 등 결제 출처 |
| `payment_method` | TEXT | 세부 결제수단 |
| `amount` | BIGINT | 결제금액 |
| `tax_amount` | BIGINT | 세액 |
| `supply_amount` | BIGINT | 공급가액 |
| `tax_exempt_amount` | BIGINT | 면세금액 |
| `approved_no` | TEXT | 승인번호 |
| `approved_at` | TIMESTAMP | 승인 시각 |
| `cancelled_at` | TIMESTAMP | 취소 시각 |
| `created_at` | TIMESTAMP | 생성 시각 |
| `updated_at` | TIMESTAMP | 변경 시각 |

### FR-DB-005. 배치 실행 로그

#### 테이블: `batch_runs`

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `run_id` | UUID PK | 배치 실행 ID |
| `merchant_id` | BIGINT | 매장 ID |
| `business_date` | DATE | 대상 영업일 |
| `status` | TEXT | `RUNNING`, `SUCCESS`, `FAILED`, `PARTIAL_SUCCESS` |
| `started_at` | TIMESTAMP | 시작 시각 |
| `finished_at` | TIMESTAMP | 종료 시각 |
| `orders_count` | INT | 주문 수 |
| `payments_count` | INT | 결제 수 |
| `csv_files_count` | INT | 생성 CSV 수 |
| `drive_upload_status` | TEXT | 업로드 상태 |
| `error_message` | TEXT | 실패 사유 |

---

## 5.3 pandas 분석 요구사항

### FR-ANL-001. 일별 매출 요약

CSV 파일명 예시:

```text
daily_sales_summary_YYYY-MM-DD.csv
```

| 지표 | 계산식 |
|---|---|
| 총매출 | 완료 주문의 `total_amount` 합계 |
| 순매출 | 완료 주문 매출 - 취소 주문 매출 |
| 주문 수 | 완료 주문 수 |
| 취소 주문 수 | 취소 주문 수 |
| 취소율 | 취소 주문 수 / 전체 주문 수 |
| 객단가 | 총매출 / 완료 주문 수 |
| 할인 총액 | `discount_amount` 합계 |
| 할인율 | 할인 총액 / 원금액 합계 |
| 공급가액 | `supply_amount` 합계 |
| 세액 | `tax_amount` 합계 |

### FR-ANL-002. 시간대별 매출 분석

CSV 파일명 예시:

```text
hourly_sales_YYYY-MM-DD.csv
```

목적은 피크 시간대, 인력 배치, 재료 준비, 브레이크타임 운영 판단에 활용하는 것이다.

| 컬럼 | 설명 |
|---|---|
| `business_date` | 영업일 |
| `hour` | 0~23시 |
| `sales_amount` | 시간대 매출 |
| `orders_count` | 주문 수 |
| `avg_order_value` | 객단가 |
| `cancelled_orders_count` | 취소 주문 수 |

### FR-ANL-003. 상품별 매출 분석

CSV 파일명 예시:

```text
item_sales_YYYY-MM-DD.csv
```

| 지표 | 설명 |
|---|---|
| 판매수량 | 상품별 `quantity` 합계 |
| 상품매출 | 상품별 매출 합계 |
| 매출기여율 | 상품매출 / 전체매출 |
| 평균단가 | 상품매출 / 판매수량 |
| 전일 대비 판매량 증감 | 오늘 판매량 - 전일 판매량 |
| 전주 동일 요일 대비 증감률 | 오늘 매출 / 전주 동일 요일 매출 - 1 |

### FR-ANL-004. 카테고리별 매출 분석

CSV 파일명 예시:

```text
category_sales_YYYY-MM-DD.csv
```

| 지표 | 설명 |
|---|---|
| 카테고리 매출 | 카테고리별 매출 합계 |
| 주문 포함 횟수 | 해당 카테고리가 포함된 주문 수 |
| 판매수량 | 카테고리별 수량 합계 |
| 매출기여율 | 카테고리 매출 / 전체매출 |

### FR-ANL-005. 결제수단별 매출 분석

CSV 파일명 예시:

```text
payment_method_sales_YYYY-MM-DD.csv
```

| 지표 | 설명 |
|---|---|
| 결제수단 | 현금, 카드, 간편결제, 계좌이체 등 |
| 승인금액 | 승인 결제 합계 |
| 취소금액 | 취소 결제 합계 |
| 순결제금액 | 승인금액 - 취소금액 |
| 결제건수 | 결제 건수 |
| 비중 | 결제수단별 금액 / 전체 결제금액 |

### FR-ANL-006. 경영 관리 알림용 지표 생성

CSV 파일명 예시:

```text
management_alerts_YYYY-MM-DD.csv
```

| 알림 유형 | 조건 예시 |
|---|---|
| 매출 급감 | 전주 동일 요일 대비 매출 -20% 이하 |
| 취소율 이상 | 취소율 5% 초과 |
| 할인 과다 | 할인율 15% 초과 |
| 특정 상품 급감 | 전주 동일 요일 대비 판매량 -30% 이하 |
| 피크 집중 | 상위 2개 시간대 매출이 일매출의 50% 초과 |
| 저성과 상품 | 최근 7일 판매수량 0 또는 기준 이하 |

---

## 5.4 CSV 생성 및 Google Drive 업로드

### FR-CSV-001. CSV 생성

매일 아래 파일을 생성한다.

| 파일명 | 설명 |
|---|---|
| `raw_orders_YYYY-MM-DD.csv` | 주문 원천 정규화 데이터 |
| `raw_payments_YYYY-MM-DD.csv` | 결제 원천 정규화 데이터 |
| `daily_sales_summary_YYYY-MM-DD.csv` | 일별 매출 요약 |
| `hourly_sales_YYYY-MM-DD.csv` | 시간대별 매출 |
| `item_sales_YYYY-MM-DD.csv` | 상품별 매출 |
| `category_sales_YYYY-MM-DD.csv` | 카테고리별 매출 |
| `payment_method_sales_YYYY-MM-DD.csv` | 결제수단별 매출 |
| `management_alerts_YYYY-MM-DD.csv` | 경영 관리 알림 지표 |

#### CSV 규칙

| 항목 | 규칙 |
|---|---|
| 인코딩 | `utf-8-sig` 권장 |
| 구분자 | comma |
| 날짜 형식 | `YYYY-MM-DD` |
| 시각 형식 | ISO 8601 |
| 금액 | 원 단위 정수 |
| 파일명 | `{merchant_name}_{report_type}_{business_date}.csv` |
| 재생성 | 동일 파일 재생성 시 버전 suffix 또는 Drive 파일 교체 정책 적용 |

### FR-GDRIVE-001. Google Drive 업로드

CSV 파일 크기와 네트워크 안정성에 따라 Google Drive 업로드 방식을 선택한다.

| 파일 크기/상황 | 업로드 방식 |
|---|---|
| 5MB 이하 일반 CSV | `multipart` |
| 5MB 초과 또는 네트워크 실패 가능성 높음 | `resumable` |
| 메타데이터 불필요한 단순 파일 | `media` 가능하나 MVP에서는 기본 미사용 |

#### 업로드 폴더 구조 예시

```text
Google Drive/
  POS_Sales_Reports/
    merchant_{merchant_id}_{merchant_name}/
      2026/
        06/
          2026-06-17/
            raw_orders_2026-06-17.csv
            raw_payments_2026-06-17.csv
            daily_sales_summary_2026-06-17.csv
            hourly_sales_2026-06-17.csv
            item_sales_2026-06-17.csv
            category_sales_2026-06-17.csv
            payment_method_sales_2026-06-17.csv
            management_alerts_2026-06-17.csv
```

### FR-GDRIVE-002. 업로드 결과 저장

#### 테이블: `drive_uploads`

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `id` | UUID PK | 내부 ID |
| `run_id` | UUID FK | 배치 실행 ID |
| `merchant_id` | BIGINT | 매장 ID |
| `business_date` | DATE | 영업일 |
| `report_type` | TEXT | 리포트 유형 |
| `file_name` | TEXT | CSV 파일명 |
| `drive_file_id` | TEXT | Google Drive 파일 ID |
| `drive_folder_id` | TEXT | 업로드 폴더 ID |
| `file_size_bytes` | BIGINT | 파일 크기 |
| `checksum` | TEXT | 파일 해시 |
| `status` | TEXT | `SUCCESS`, `FAILED` |
| `uploaded_at` | TIMESTAMP | 업로드 시각 |

---

## 6. 배치 프로세스

## 6.1 일일 배치 흐름

```text
[Scheduler]
   ↓
[대상 매장/영업일 계산]
   ↓
[Toss Place 주문 목록 조회]
   ↓
[Raw JSON 저장]
   ↓
[orders / order_line_items / payments upsert]
   ↓
[pandas 분석]
   ↓
[CSV 생성]
   ↓
[Google Drive 업로드]
   ↓
[배치 로그 저장 및 알림]
```

## 6.2 스케줄 정책

| 항목 | 정책 |
|---|---|
| 실행 시점 | 매장 영업 종료 후 30~60분 뒤 |
| 타임존 | `Asia/Seoul` |
| 영업일 기준 | 매장 영업 시작~종료 기준 |
| 익일 마감 매장 | 예: 18:00~02:00이면 영업일은 시작일 기준으로 계산 |
| 보정 버퍼 | 결제/취소 지연 반영을 위해 종료 후 30~60분 추가 조회 |
| 재실행 | 동일 영업일 재실행 가능해야 함 |
| 재처리 | 과거 N일 재처리 CLI 제공 |

## 6.3 멱등성

1. `orders.order_id`를 primary key로 사용한다.
2. `payments.payment_id`를 primary key로 사용한다.
3. 동일 영업일 배치를 여러 번 실행해도 중복 데이터가 생성되지 않아야 한다.
4. Google Drive 업로드는 `business_date + merchant_id + report_type + checksum` 기준으로 중복 여부를 판단한다.
5. 웹훅을 보조 수단으로 사용할 경우, 웹훅 ID를 멱등 키로 사용한다.

---

## 7. 웹훅 활용 범위

MVP의 주 데이터 수집 방식은 **일일 배치 조회**로 한다. 다만 아래 목적으로 웹훅을 선택적으로 도입할 수 있다.

| 활용 | 설명 |
|---|---|
| 주문/결제 변경 감지 | 영업 종료 후 취소·수정 발생 감지 |
| 누락 보정 | 배치 수집 전후 발생 이벤트 기록 |
| 실시간 알림 확장 | 향후 매출 알림, 주문 급증 알림 등에 사용 |

웹훅을 도입할 경우 수신 측은 서명 검증, 재시도 대응, 멱등 처리를 고려해야 한다.

---

## 8. 데이터 품질 요구사항

### DQ-001. 주문-결제 금액 정합성

| 검증 | 조건 |
|---|---|
| 주문 총액 vs 결제 총액 | 완료 주문의 총액과 승인 결제 합계 비교 |
| 취소 주문 | 주문 상태 또는 결제 상태의 취소 여부 반영 |
| 음수 할인 | 할인금액 부호 정책 통일 |
| 세액 | 주문/결제 세액 합계 차이 검증 |

### DQ-002. 누락 데이터 검증

| 검증 | 조건 |
|---|---|
| 주문 ID 누락 | `order_id` null 불가 |
| 매장 ID 누락 | `merchant_id` null 불가 |
| 주문 시간 누락 | `created_at` null 불가 |
| 상품명 누락 | 상품 분석에서 `item_title` null인 경우 `UNKNOWN` 처리 |
| 결제수단 누락 | `payment_method` null이면 `UNDEFINED` 처리 |

### DQ-003. 이상치 탐지

| 이상치 | 예시 |
|---|---|
| 매출 급증 | 최근 4주 동일 요일 평균 대비 +200% |
| 매출 급감 | 최근 4주 동일 요일 평균 대비 -50% |
| 객단가 이상 | 평균 대비 3표준편차 초과 |
| 취소율 이상 | 최근 7일 평균 대비 2배 이상 |

---

## 9. 비기능 요구사항

| 구분 | 요구사항 |
|---|---|
| 신뢰성 | 일일 배치 성공률 99% 이상 목표 |
| 재처리성 | 특정 매장/영업일 단위 재실행 가능 |
| 성능 | 매장 1개, 주문 10,000건 기준 30분 이내 처리 |
| 보안 | API key, Google credential은 Secret Manager에 저장 |
| 개인정보 | 카드번호 등 마스킹된 값만 저장하고 불필요한 식별 정보 저장 금지 |
| 감사 추적 | API 요청 파라미터, 응답 상태, trace ID, 업로드 파일 ID 저장 |
| 관측성 | 로그, 메트릭, 실패 알림 제공 |
| 확장성 | 다중 매장, 다중 Drive 폴더, 다중 리포트 유형 지원 |

---

## 10. 권장 기술 스택

| 영역 | 권장안 |
|---|---|
| 언어 | Python 3.11+ |
| 데이터 분석 | pandas |
| API 클라이언트 | `httpx` 또는 `requests` |
| DB | PostgreSQL |
| ORM/마이그레이션 | SQLAlchemy + Alembic |
| 스케줄러 | cron, Airflow, Prefect, Cloud Scheduler 중 택1 |
| 파일 생성 | pandas `to_csv()` |
| Google Drive | Google Drive API v3 |
| 배포 | Docker |
| Secret | GCP Secret Manager, AWS Secrets Manager, Vault 등 |
| 모니터링 | Sentry, Cloud Logging, Grafana/Prometheus 등 |

---

## 11. API 및 모듈 설계

### 11.1 주요 모듈

```text
src/
  config/
    settings.py
  clients/
    toss_place_client.py
    google_drive_client.py
  db/
    models.py
    repositories.py
    migrations/
  services/
    business_date_service.py
    ingestion_service.py
    normalization_service.py
    analytics_service.py
    csv_export_service.py
    drive_upload_service.py
    batch_service.py
  jobs/
    daily_sales_job.py
    backfill_job.py
  tests/
```

### 11.2 주요 클래스 책임

| 클래스/서비스 | 책임 |
|---|---|
| `TossPlaceClient` | 인증 헤더 구성, 주문/결제 API 호출, rate limit 대응 |
| `BusinessDateService` | 매장별 영업일 시작/종료 시각 계산 |
| `IngestionService` | 페이지네이션 조회, raw 저장 |
| `NormalizationService` | JSON → 정규화 테이블 변환 |
| `AnalyticsService` | pandas 분석 지표 생성 |
| `CsvExportService` | CSV 파일 생성, checksum 계산 |
| `GoogleDriveClient` | 폴더 확인/생성, 파일 업로드 |
| `BatchService` | 전체 워크플로우 오케스트레이션 |

---

## 12. 분석 산출물 상세

### 12.1 `daily_sales_summary`

| 컬럼 | 설명 |
|---|---|
| `merchant_id` | 매장 ID |
| `business_date` | 영업일 |
| `gross_sales` | 완료 주문 총매출 |
| `net_sales` | 취소 반영 순매출 |
| `orders_count` | 완료 주문 수 |
| `cancelled_orders_count` | 취소 주문 수 |
| `cancel_rate` | 취소율 |
| `avg_order_value` | 객단가 |
| `discount_amount` | 할인 총액 |
| `discount_rate` | 할인율 |
| `tax_amount` | 세액 |
| `supply_amount` | 공급가액 |

### 12.2 `item_sales`

| 컬럼 | 설명 |
|---|---|
| `item_title` | 상품명 |
| `category_title` | 카테고리 |
| `quantity_sold` | 판매수량 |
| `sales_amount` | 상품 매출 |
| `sales_share` | 매출 비중 |
| `avg_unit_price` | 평균 판매 단가 |
| `dod_quantity_change` | 전일 대비 수량 변화 |
| `wow_sales_growth_rate` | 전주 동일 요일 대비 매출 증감률 |

### 12.3 `hourly_sales`

| 컬럼 | 설명 |
|---|---|
| `hour` | 시간대 |
| `sales_amount` | 매출 |
| `orders_count` | 주문 수 |
| `avg_order_value` | 객단가 |
| `sales_share` | 시간대 매출 비중 |

### 12.4 `management_alerts`

| 컬럼 | 설명 |
|---|---|
| `alert_type` | 알림 유형 |
| `severity` | `LOW`, `MEDIUM`, `HIGH` |
| `metric_name` | 지표명 |
| `metric_value` | 현재 값 |
| `baseline_value` | 비교 기준 |
| `message` | 사람이 읽을 수 있는 설명 |

---

## 13. 성공 지표

| 지표 | 목표 |
|---|---|
| 일일 배치 성공률 | 99% 이상 |
| CSV 업로드 성공률 | 99% 이상 |
| 중복 주문 저장률 | 0% |
| 주문-결제 금액 불일치율 | 0.5% 이하, 초과 시 알림 |
| 배치 완료 시간 | 영업 종료 후 1시간 이내 |
| 재처리 가능 범위 | 최근 90일 이상 |
| 리포트 생성 파일 수 | 매장별 최소 8개 CSV |

---

## 14. 에러 처리 및 알림

| 상황 | 처리 |
|---|---|
| API 401 | 인증 실패. 즉시 실패 처리 및 운영자 알림 |
| API 429 | rate limit 대기 후 재시도 |
| API 5xx | 최대 N회 재시도 후 실패 |
| DB 저장 실패 | 트랜잭션 rollback, 실패 로그 저장 |
| CSV 생성 실패 | 해당 리포트만 실패 처리, 전체 배치 상태는 `PARTIAL_SUCCESS` 가능 |
| Drive 업로드 실패 | 재시도 후 실패 시 로컬/스토리지에 파일 보존 |
| 데이터 정합성 실패 | CSV는 생성하되 `data_quality_warnings.csv` 추가 생성 |

---

## 15. 보안 및 개인정보 요구사항

1. Toss API key와 Google credential은 코드, Git, CSV에 포함하지 않는다.
2. 운영 로그에 secret, 승인번호 전체, 개인 식별 가능 정보가 남지 않도록 마스킹한다.
3. Google Drive 폴더 권한은 최소 권한 원칙으로 관리한다.
4. DB 접근 계정은 읽기/쓰기 권한을 분리한다.
5. 원천 JSON 보존 기간을 정한다. 예: 1년 보관 후 아카이브.
6. 카드번호 등 결제 상세 정보는 마스킹된 값만 저장하고, 분석에 불필요하면 저장하지 않는다.

---

## 16. MVP 범위

### 포함

1. 단일 또는 다중 매장 설정
2. Toss Place 주문 목록 수집
3. 주문/상품/결제 정규화 저장
4. pandas 기반 8개 CSV 생성
5. Google Drive 업로드
6. 배치 로그 및 실패 재시도
7. 특정 영업일 재처리 CLI

### 제외

1. 실시간 웹 대시보드
2. Slack/Kakao 알림
3. ML 수요 예측
4. 자동 발주 추천
5. 회계 시스템 자동 전표 연동
6. Toss POS 화면 내 플러그인 UI

---

## 17. 개발 마일스톤

| 단계 | 산출물 |
|---|---|
| M1. 설계 | DB 스키마, API 연동 방식, CSV 스펙 확정 |
| M2. Toss API 연동 | 주문 목록 수집, 페이지네이션, raw 저장 |
| M3. DB 정규화 | orders, order_line_items, payments 저장 |
| M4. pandas 분석 | 일별/시간대별/상품별/결제수단별 분석 |
| M5. CSV 생성 | 파일명 규칙, 인코딩, checksum |
| M6. Google Drive 업로드 | 폴더 구조, 업로드 로그 |
| M7. 배치 운영화 | 스케줄러, 재시도, 알림, 재처리 CLI |
| M8. QA | 샘플 데이터 검증, 장애 시나리오, 운영 문서 |

---

## 18. QA 및 인수 기준

### 18.1 기능 인수 기준

| ID | 기준 |
|---|---|
| AC-001 | 지정한 매장과 영업일에 대해 주문 데이터가 DB에 저장된다 |
| AC-002 | 동일 영업일 배치를 2회 실행해도 주문/결제 중복이 없다 |
| AC-003 | 완료 주문과 취소 주문이 구분 저장된다 |
| AC-004 | 일별 매출 요약 CSV가 생성된다 |
| AC-005 | 상품별, 시간대별, 결제수단별 CSV가 생성된다 |
| AC-006 | CSV가 Google Drive 지정 폴더에 업로드된다 |
| AC-007 | 업로드된 파일의 Drive file ID가 DB에 저장된다 |
| AC-008 | API 실패 시 배치 로그에 실패 사유가 저장된다 |
| AC-009 | 과거 특정 날짜를 재처리할 수 있다 |
| AC-010 | 주문-결제 금액 불일치가 감지되면 품질 경고가 생성된다 |

### 18.2 데이터 검증 기준

1. Toss 원천 주문 수와 DB 주문 수가 일치해야 한다.
2. 완료 주문 총매출과 CSV 총매출이 일치해야 한다.
3. 결제수단별 금액 합계와 일별 총 결제금액이 일치해야 한다.
4. 상품별 매출 합계와 주문 항목 기준 총액이 허용 오차 내 일치해야 한다.
5. 취소 주문은 총매출과 순매출 계산에서 명확히 분리되어야 한다.

---

## 19. 주요 리스크와 대응

| 리스크 | 대응 |
|---|---|
| Toss API 호출 제한 | 매장별 rate limiter, 429 재시도, 페이지 크기 최적화 |
| 영업일 경계 오류 | 매장별 영업시간 설정, 익일 종료 케이스 테스트 |
| 취소/환불 지연 반영 | 종료 후 버퍼 조회, D+1 재보정 배치 |
| Google Drive 업로드 실패 | resumable upload, 재시도, 로컬 파일 보존 |
| 데이터 중복 | 주문 ID/결제 ID 기준 upsert |
| API 스펙 변경 | raw JSON 저장, 계약 테스트, Toss 변경 이력 주기 확인 |
| 불안정 필드 의존 | 핵심 분석은 안정 필드 중심으로 설계 |

---

## 20. 권장 MVP 구현 순서

가장 먼저 **주문 목록 수집 → raw 저장 → 정규화 저장**을 완성하는 것이 좋다. 그다음 pandas 분석과 CSV 생성을 붙이고, 마지막에 Google Drive 업로드와 운영 로그를 붙이는 순서가 안정적이다.

```text
1차: Toss 주문 목록 수집 + DB 저장
2차: 주문/결제/상품 정규화
3차: pandas 분석 CSV 생성
4차: Google Drive 업로드
5차: 배치 스케줄링, 재시도, 재처리 CLI
6차: 데이터 품질 검증 및 운영 알림
```

---

## 21. 참고 문서

- Toss Place POS Integration Getting Started: https://docs.tossplace.com/guide/pos-integration/getting-started.html
- Toss Place Open API Intro: https://docs.tossplace.com/guide/pos-integration/open-api/intro.html
- Toss Place Open API Common: https://docs.tossplace.com/reference/open-api/common.html
- Toss Place Order Methods: https://docs.tossplace.com/reference/open-api/order/order-methods.html
- Toss Place Order Model: https://docs.tossplace.com/reference/open-api/order/order-model.html
- Toss Place Merchant API: https://docs.tossplace.com/reference/open-api/merchant.html
- Toss Place Webhook API: https://docs.tossplace.com/reference/open-api/webhook.html
- Google Drive API Uploads: https://developers.google.com/workspace/drive/api/guides/manage-uploads
- Google Drive API files.create: https://developers.google.com/workspace/drive/api/reference/rest/v3/files/create
