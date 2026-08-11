# Báo cáo Day 13 Observability

## 1. Thông tin nhóm

- Tên nhóm: **K4 — T104**
- Repository URL: `https://github.com/QuocAnh-Nguyen/Day13-K4-Observability-T104.git`
- Commit SHA cuối: `5960c795d94a3d24972c719fb33a5320474f3ebf` (HEAD trên `main`, sẵn sàng push lên `origin`)
- Thành viên và vai trò:
  - **Nguyễn Quốc Anh** (`quocanhaddc@gmail.com`) — Logging & PII; Tracing & Prompt versioning.
  - **Hoàng Bảo Huy** (`justmine959@gmail.com`) — Dashboard, SLO & Alerts.
  - **Trương Ái Linh** (`truongailinh277@gmail.com`) — Incident, Report & Evidence.

## 2. Kết quả kỹ thuật

- Điểm `validate_logs.py`: **100/100** (xem `submission/evidence/validate_logs.txt`).
- Tổng số traces: **≥ 10** (baseline `--concurrency 5` + challenge; danh sách 10 trace tại `submission/evidence/trace_ids.txt`; 5 trace challenge tại `challenge_evidence.txt`).
- Số PII leak còn lại: **0** (`validate_logs.txt`: "Potential PII leaks detected: 0").
- Link/đường dẫn dashboard: Streamlit `python scripts/dashboard.py` (đọc `data/logs.jsonl`); bản static 6 panel tại `submission/evidence/dashboard_6panels.png`.

## 3. Logging và tracing

- Evidence correlation ID: `submission/evidence/correlation_id_logs.jsonl` — cùng `req-b43be1b9` trên cả `request_received` và `response_sent`.
- Evidence PII redaction: `submission/evidence/pii_redaction.txt` — input thô vs giá trị đã scrub (`[REDACTED_EMAIL]`, `[REDACTED_PHONE_VN]`, `[REDACTED_PASSPORT_VN]`, `[REDACTED_CREDIT_CARD]`).
- Evidence trace waterfall: `submission/evidence/trace_waterfall.json` — trace `893225ea098f2ed2799a3d2e55634276`.
- Giải thích một span đáng chú ý: span `GENERATION "run"` (`db2bfe93954f606a`) gộp toàn bộ pipeline (RAG retrieve + LLM) nên baseline latency 0.151s; khi bật `rag_slow` span tương tự tăng lên ~2.65s và là nơi khoanh vùng root cause (xem mục 6).

## 4. Prompt versioning

- Prompt name: `day13-chat`
- Version/label baseline: v1, label `production` (+ `baseline`), nội dung baseline
- Version/label candidate: v2, label `candidate`, nội dung thay đổi rõ ràng
- Trace ID của mỗi version: metadata prompt `name=day13-chat label=production version=1` hiển thị trong các trace `893225ea098f2ed2799a3d2e55634276`, `68712100f6f6b00febcd41b160168b25`, `5a48fd6a6cc4197f5b7cb2688b2a0f1b` (xem `prompt_versions.txt`).
- Bằng chứng đổi label hoặc rollback: `submission/evidence/prompt_rollback.txt` — label `production` v1 → v2 → v1.

## 5. Dashboard, SLO và alerts

- Kết quả `validate_dashboard.py`: **HỢP LỆ: 6/6 panel** (`submission/evidence/validate_dashboard.txt`).
- Evidence dashboard: `submission/evidence/dashboard_6panels.png` (6 panel: latency p50/p95/p99, traffic, error, cost, tokens, quality; mỗi panel có SLO line + đơn vị).
- SLO đã chọn và lý do: `config/slo.yaml` — `latency_p95_ms ≤ 3000ms` (ngưỡng tương ứng incident rag_slow 2.5s), `error_rate_pct ≤ 2%`, `daily_cost_usd ≤ 2.5USD`, `quality_score_avg ≥ 0.75`.
- Alert rules và runbook: `config/alert_rules.yaml` (3 alert symptom-based: `high_latency_p95`, `error_rate_breach`, `cost_breach`) + runbook tại `docs/alerts.md#alert-1/2/3`.

## 6. Điều tra challenge

- Challenge ID: `day13-k4-observability-v1` (cohort K4, `config/challenge.json`).
- Triệu chứng từ metrics: toàn bộ 5 query `monitoring` có `latency_ms ≈ 2650ms > 2000ms` (ngưỡng `latency_threshold_ms`), 5/5 là BREACH.
- Trace ID liên quan: `073809f8247ec45ca8e25ee17ff73ec2`, `86ed68b0678911c18f12cdf83e03fd4f`, `768f2c76de5a52e3949babcc35dbb3b7`, `e03ca62918053bae27b2303c11ded0b9`, `5822e893884f1ff42f9bc6d8412bef9e`.
- Log line/correlation ID liên quan: `req-2d56ae10`, `req-5a5b7db6`, `req-800b3b18`, `req-4e2d141d`, `req-8fc49d4f` — log `response_sent` có `latency_ms ≥ 2650` (chi tiết tại `submission/evidence/challenge_evidence.txt`).
- Root cause: `STATE["rag_slow"]` thêm `time.sleep(2.5)` trong `app/mock_rag.retrieve()` (app/mock_rag.py:17-18), làm mọi request feature `monitoring` vượt ngưỡng 2000ms.
- Fix action: tắt incident `rag_slow` (`scripts/inject_incident.py --disable`); loại bỏ delay trong `mock_rag.retrieve`.
- Preventive measure: đặt timeout/circuit-breaker cho bước retrieve; theo dõi SLO `latency_p95` qua alert `high_latency_p95` để phát hiện sớm, không đợi user phàn nàn.

## 7. Đóng góp cá nhân

| Thành viên | Phần việc | Commit/PR | Điều đã học |
|---|---|---|---|
| Nguyễn Quốc Anh | Logging & PII (middleware correlation ID, contextvars, scrub_event trước JSON renderer), Tracing & Prompt versioning (seed `day13-chat` v1/v2 trong Langfuse, rollback) | `f041008`, `7a35846` | Vòng đời contextvar structlog, thứ tự PII processor trước renderer, prompt label/version trong trace khi đổi label không làm mất version. |
| Hoàng Bảo Huy | Dashboard Streamlit 6 panel, SLO, alert rules, runbook | `77a2276` | Cách tính percentile p50/p95/p99, contract panel trong `dashboard.yaml`, cách thiết kế alert symptom-based khớp SLO. |
| Trương Ái Linh | Chạy load/challenge, chụp evidence, soạn báo cáo | `030537d` | Luồng điều tra Metrics → Traces → Logs → root cause và cách dẫn chứng mỗi claim bằng trace ID + log line + metric. |
