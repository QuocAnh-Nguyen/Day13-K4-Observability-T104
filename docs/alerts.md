# Alert và Runbook

Mỗi alert dựa trên triệu chứng người dùng hoặc SLO, không dựa trực tiếp vào tên implementation nội bộ.

## Alert 1 — High latency p95

- Tên: `high_latency_p95`
- Severity: critical
- SLI/SLO liên quan: `latency_p95_ms` ≤ 3000 ms (SLO `config/slo.yaml:4`)
- Điều kiện và thời gian duy trì: p95 latency > 3000 ms trong 2 lượt đánh giá liên tiếp (mỗi lượt ~30s/panel window 60 phút)
- Ảnh hưởng tới người dùng: câu trả lời bị chậm rõ rệt ở các request bình thường
- Ba bước kiểm tra đầu tiên:
  1. Mở dashboard panel Latency để xác nhận p95 vượt ngưỡng trong cửa sổ 60 phút.
  2. Mở trace chậm nhất trong Langfuse, tìm span có thời lượng lớn.
  3. Tra log theo `correlation_id` đúng trace đó, kiểm tra span nghi ngờ.
- Mitigation tạm thời: tắt incident nếu là practice (`scripts/inject_incident.py --scenario rag_slow --disable`), hoặc giảm tải/scale worker.
- Owner: platform

## Alert 2 — Error rate breach

- Tên: `error_rate_breach`
- Severity: high
- SLI/SLO liên quan: `error_rate_pct` ≤ 2% (SLO `config/slo.yaml:8`)
- Điều kiện và thời gian duy trì: error rate > 2% trong 2 lượt đánh giá liên tiếp
- Ảnh hưởng tới người dùng: một phần request trả lỗi hoặc thất bại khi gọi API
- Ba bước kiểm tra đầu tiên:
  1. Mở dashboard panel Error rate để xem breakdown theo `error_type`.
  2. Mở trace của yêu cầu thất bại, khoanh vùng span lỗi.
  3. Tra log `request_failed` theo `correlation_id` để đọc chi tiết exception.
- Mitigation tạm thời: nếu do `tool_fail` practice, tắt incident; với lỗi thật, tăng retry/timeout và kiểm tra service downstream.
- Owner: platform

## Alert 3 — Cost breach

- Tên: `cost_breach`
- Severity: medium
- SLI/SLO liên quan: `daily_cost_usd` ≤ 2.5 USD (SLO `config/slo.yaml:11`)
- Điều kiện và thời gian duy trì: tổng `cost_usd` vượt 2.5 USD trong cửa sổ 60 phút
- Ảnh hưởng tới người dùng: chi phí vận hành tăng đột biến, có thể kéo dài hoá đơn
- Ba bước kiểm tra đầu tiên:
  1. Mở dashboard panel Cost, xem tổng theo phút trong cửa sổ.
  2. Mở trace có `cost_details.total` lớn, kiểm tra số token input/output.
  3. Tra log `response_sent` theo `correlation_id` để đối chiếu `tokens_in/out` và `cost_usd`.
- Mitigation tạm thời: dừng traffic tăng, giảm `max_tokens`/độ dài câu trả lời; với `cost_spike` practice, tắt incident.
- Owner: platform
