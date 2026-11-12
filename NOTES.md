# Ghi chú phân tích · Day 04

## 1. Case được chọn và bằng chứng trong trace

**Case chọn: H6 (điểm 60), kèm H1 (điểm 60) có cùng lỗi.** Số liệu trong mục này lấy từ `eval_before.json`.

- **Câu hỏi H6:** xem status GitHub Actions, tìm incident `h02n9572mx9y`, *theo liên kết của nó*, rồi cho biết incident gộp có nêu nguyên nhân hay chỉ mô tả ảnh hưởng.
- **Câu trả lời của agent:** "Sự cố không nêu rõ nguyên nhân cụ thể ... chủ yếu đề cập đến việc điều tra và theo dõi ổn định."
- **Trace H6:** chỉ có 2 model step (`tool_calls` rồi `stop`). Cả 2 tool call (`get_status` github "actions" và `get_incident_details` github `h02n9572mx9y`) nằm ở step 1. Không có call nào cho `xr3myzj7ms51`.
- **Dữ liệu agent đã nhận được:** update `resolved` (2026-11-04T18:16:08Z) của `h02n9572mx9y` có nội dung: "Please refer to the combined summary in this related incident: https://www.githubstatus.com/incidents/xr3myzj7ms51". Update này `truncated: false`, nên link nằm sẵn trong kết quả tool.
- **Kiểm tra bằng code của runner:** `grade.mechanical.violations = ["Cần tool calls ở ít nhất 2 model steps khác nhau."]`, `score_cap = 60`.
- **H1 cùng lỗi:** hai `get_incident_details` (GitHub `h02n9572mx9y`, Cloudflare `yaa0witbl2me`) đều ở step 1, `tool_steps = 1`, không đọc `xr3myzj7ms51`, `score_cap = 60`.

**Các lỗi khác thấy trong eval trước cải tiến:**

| Case | Điểm | Lỗi |
|---|---|---|
| A5 | 30 | Khẳng định "không có sự cố nào của Actions trong 2025" dù tool chỉ xem được 50 incident gần nhất |
| E6 | 45 | Tool timeout, câu trả lời mơ hồ, không nói rõ là không lấy được dữ liệu |
| E1 | 65 | Liệt kê incident cũ mà không nhấn mạnh là đã resolved |
| H7 | 70 | Kết luận trạng thái Cloudflare dựa trên giả định khi thiếu dữ liệu |
| A7 | 70 | Chưa tách rõ phạm vi địa lý (Phoenix) với ảnh hưởng tới Việt Nam |

**Kiểm tra nguồn trên trace trước cải tiến:** trong 15 case có `get_incident_details` thành công, answer của cả 15 case đều chứa ít nhất một URL incident đã quan sát. Vì vậy cơ chế kiểm tra nguồn (mục 3b) không sửa lỗi nào trong lần chạy đó.

## 2. Nguyên nhân có thể

- **Không phải giới hạn dữ liệu:** link tới `xr3myzj7ms51` có trong kết quả tool, và incident này nằm trong 50 incident gần nhất.
- **Không phải lỗi của agent loop:** loop gọi tool, trả đúng `tool_call_id` và tiếp tục gọi model; model chủ động trả `stop` ở step 2.
- **Nhiều khả năng là prompt/hành vi model:** `SYSTEM_PROMPT` cũ chỉ dặn "dùng tools trước khi khẳng định", không nói phải theo dõi incident được dẫn tới. `gpt-4o-mini` dừng sau khi đọc incident đầu và rút ra kết luận phủ định ("không nêu nguyên nhân") từ bằng chứng chưa đầy đủ.
- **Kết quả thử lần 1:** thêm quy tắc vào prompt không đủ. H1 và H6 vẫn 60 điểm, vẫn 2 model step, và model có nhắc ID `xr3myzj7ms51` trong answer nhưng không gọi tool cho nó (chạy riêng H6 4 lần đều 60). Vì vậy lần 2 chuyển sang ép ở code (mục 3d).
- **Giới hạn của chẩn đoán:** đây là giả thuyết từ trace; điểm judge chỉ là ước lượng.

## 3. Thay đổi đã implement (`agent.py`)

**a) Quy tắc theo dõi incident được dẫn tới trong prompt (lần 1).** Nếu một update dẫn tới incident khác (URL dạng `/incidents/<id>`) và câu hỏi cần nguyên nhân, ảnh hưởng hoặc đối chiếu, phải gọi `get_incident_details` cho incident đó trước khi kết luận. Không hard-code ID incident nào. Kết quả: không đủ (mục 2).

**b) Kiểm tra nguồn (theo contract trong `tests/test_validation.py`).**
- Lưu URL từ mỗi `get_incident_details` thành công (`ok` là `True` và có `url`).
- Khi model trả final answer (`finish == 'stop'`) mà answer không chứa URL nào đã lưu: ghi event `validation_missing_source` vào trace, thêm một message yêu cầu model dẫn nguồn (kèm danh sách URL), rồi gọi model tiếp trong `max_steps`.
- Hết `max_steps` mà vẫn thiếu URL thì trả `max_steps`, không báo `completed`.
- Không bắt URL khi chưa có incident nào đọc thành công (tool lỗi, hoặc chỉ gọi `get_status`).

**c) Viết lại `SYSTEM_PROMPT` theo nhóm lỗi (lần 2).** Sáu quy tắc, mỗi quy tắc nhắm vào một nhóm lỗi thấy trong eval:
1. Câu hỏi về tình trạng "hiện tại" phải gọi `get_status`; sửa tiền đề sai (H2).
2. Tool lỗi hoặc timeout thì nêu rõ phần nào không kiểm tra được, không suy đoán, kể cả khi người dùng yêu cầu cứ kết luận bình thường (E6, A3, H7).
3. Tool chỉ thấy 50 incident gần nhất mỗi provider; không kết luận "không có incident" chỉ vì không tìm thấy, không bịa danh sách hay link (A5, H8, E5).
4. Theo dõi incident được dẫn tới (H1, H6).
5. Nêu giới hạn khi dữ liệu bị cắt.
6. Chỉ dùng URL có trong kết quả tool, dẫn nguồn theo từng provider.
Cũng nhấn mạnh phân biệt incident đang diễn ra với đã resolved (E1).

**d) Ép đọc incident được dẫn tới ở code (lần 2, giải quyết H1/H6).**
- Sau mỗi `get_incident_details` thành công, quét nội dung update để tìm URL `/incidents/<id>` của GitHub hoặc Cloudflare (host được ánh xạ sang provider từ `status_tools.HOSTS`, host lạ bị bỏ qua).
- Khi model trả final answer mà còn incident được dẫn tới nhưng chưa đọc, và còn step: ghi event `validation_unread_reference`, thêm message mời model gọi `get_incident_details` cho các incident đó (nếu câu hỏi cần) rồi trả lời lại. Chỉ nhắc một lần mỗi run, tối đa 3 incident.
- Lượt bổ sung chỉ là cải thiện thêm: nếu nó lỗi provider, bị cắt (`length`), hoặc hết `max_steps`, agent giữ câu trả lời hợp lệ đã có (event `fallback_to_draft`).
- Kiểm tra nguồn (3b) được ưu tiên trước; hết budget mà thiếu URL vẫn trả `max_steps`.

**Kiểm tra offline đã làm:** chạy với provider giả cho các tình huống: thiếu URL trong budget, thiếu URL ở giới hạn budget, tool lỗi, không gọi tool, nhắc đọc incident được dẫn và model làm theo, nhắc ở step cuối (không nhắc), provider hết dữ liệu sau khi nhắc (giữ câu trả lời cũ), host lạ, và các nhánh lỗi của CP3. Tất cả cho kết quả đúng.

**Giới hạn:**
- Kiểm tra nguồn chỉ xác nhận *có mặt* một URL, không chứng minh mọi claim đều có nguồn.
- Cơ chế (d) chỉ *mời* model đọc thêm, không buộc; model vẫn có thể bỏ qua. Nó tốn thêm một lượt gọi model ở các case có incident được dẫn.

## 4. So sánh trước và sau

| Chỉ số | Trước |  Sau |
|---|---|---|
| Điểm tổng `score_0_100` | 78.41  | **85.82** (+7.41 so với trước) |
| Model call (22 case) | 51  | 50 |
| Tool call | 54  | 41 |
| Trong đó `get_incident_details` | 35  | 22 |
| Token của agent | 47,932  | 66,632 (+39.0% so với trước) |
| Token của judge | 45,653  | 37,898 |
| Event `validation_missing_source` | 0  | 0 |
| Event `validation_unread_reference` | 0  | 2 (H1, H6) |

Provider không trả giá nên không tính chi phí tiền.

**So với trước (22 case): 11 case tăng, 6 giảm, 5 không đổi.**
- **Tăng:** A5 30→100, E6 45→100, E4 80→100, A1 80→100, H4 80→100, A3 75→85, H6 60→75, H1 60→68, E1 65→75, E3 75→80, H5 95→100.
- **Giảm:** H7 70→45, E2 100→80, A8 95→85, H8 90→80, E5 80→75, A2 100→95.
- **Không đổi:** A4, A6, A7, H2, H3. Trong đó H2 đã phục hồi từ 50 (ở lần trung gian) về 100.

**Mục tiêu H1/H6: đạt một phần.** Event `validation_unread_reference` xuất hiện ở step 2 của cả hai case (`incidents: ['github/xr3myzj7ms51']`). Sau đó model gọi `get_incident_details` cho `xr3myzj7ms51` ở step 3, nên vi phạm "2 model step" biến mất, `score_cap` không còn 60, và điểm tăng: H6 60→75, H1 60→68. Điểm chưa cao vì judge cho rằng H1 vẫn khẳng định có thể có nguyên nhân chung giữa hai incident khi chưa có bằng chứng, còn H6 chưa liên kết rõ nguyên nhân với incident ban đầu.

**Chi phí:** token của agent tăng 39.0%. Phần lớn đến từ H1 và H6 (4,992 → 19,321 token, thêm 2 step và đọc thêm incident). 20 case còn lại tăng 10.2% (42,940 → 47,311), phù hợp với việc system prompt dài hơn.

**Kiểm tra nguồn:** không kích hoạt lần nào ở lần chạy cuối (0 event), vì answer đã dẫn URL đã quan sát.
## 5. Lỗi còn lại và giới hạn

**Lỗi còn lại:**
- **H7 (70→45), tệ hơn trước:** Cloudflare `get_status` lỗi timeout, nhưng judge cho rằng answer vẫn khẳng định Cloudflare operational (boundaries = 0), dù quy tắc 2 trong prompt đã dặn ngược lại. Quy tắc trong prompt không đủ trước yêu cầu "cứ kết luận operational" của người dùng. Hướng tiếp theo (chưa làm): kiểm tra ở code, nếu có tool trả `ok: false` thì answer phải nêu lỗi của provider đó.
- **H8 (90→80):** agent không gọi tool nào (1 model step, 0 tool call), trả lời thẳng từ quy tắc trong prompt. Nội dung nêu đúng giới hạn nhưng không có bằng chứng từ snapshot. Có thể là tác dụng phụ của quy tắc 3.
- **E2 (100→80):** giảm ở cả lần trung gian và lần cuối. Ở lần cuối agent chỉ gọi `get_status`, không đọc chi tiết incident; chưa rõ nguyên nhân.
- **H1 (68):** vẫn có suy đoán về nguyên nhân chung giữa GitHub và Cloudflare khi chưa có bằng chứng.
- **A7 (70), A6 (75), E1 (75), E5 (75):** chưa xử lý thêm (phạm vi địa lý ở A7; ngày tháng và phân biệt hiện tại/lịch sử ở E1, E5).

**Giới hạn của kết quả:**
- **Mỗi phiên bản chỉ có một lần chạy cả bộ,** nên không đủ để kết luận độ ổn định. Cùng một agent, H5 được 85, 85, 100, 80 trong các lần chạy riêng; H8 có chuỗi tool call giống hệt giữa hai lần nhưng điểm là 90 và 65. Tức là điểm từng case dao động khá lớn giữa các lần.
- **Các quy tắc ở mục 3c được viết sau khi đã xem lỗi của chính 22 case này,** nên điểm lần 2 lạc quan hơn so với trên dữ liệu chưa thấy. Không có sửa `fixtures/`, `evals/cases.json`, `evals/scenarios.py`, `evals/grading.py`, và không hard-code ID incident hay đáp án nào; code chỉ dùng regex và ánh xạ host tổng quát.
- **Judge là `gpt-4o-mini`, cùng model với agent,** nên có thể thiên lệch; điểm judge chỉ là ước lượng.
- **Judge đôi khi trả JSON lỗi** (ở một lần chạy, H5 và H6 bị `unscored` vì "Illegal trailing comma"). Chạy riêng H5, H6 nhiều lần đều chấm được, nên đây là lỗi ngẫu nhiên; bộ eval được chạy lại đến khi chấm đủ 22 case và lấy lần đầu tiên chấm đủ, không chọn theo điểm.
- **Kiểm tra nguồn** chỉ xác nhận có một URL trong answer, không chứng minh mọi claim đều có nguồn.
- **Chi phí tăng 39% token** là cái giá của cải tiến H1/H6 và prompt dài hơn.
- Điểm eval và điểm bài lab là hai thang đo khác nhau.