# IIM — Hướng dẫn demo (local, không cần AWS)

Chạy toàn bộ luồng SRE trên máy local với LLM free tier và nguồn log giả lập. Không cần tài khoản
AWS, không cần SSO profile, không đụng dữ liệu thật.

Thời lượng demo: **8–10 phút**. Chuẩn bị lần đầu: ~10 phút.

---

## 1. Chuẩn bị (làm một lần)

**Lấy 2 API key miễn phí:**

| Dịch vụ | Dùng để | Lấy ở đâu |
|---|---|---|
| OpenRouter | Phân tích incident (LLM) | [openrouter.ai/keys](https://openrouter.ai/keys) — chọn model có đuôi `:free` |
| Jina | Embedding cho RAG | [jina.ai/embeddings](https://jina.ai/embeddings) — free tier |

**Điền vào `.env` ở thư mục gốc** (file đã có sẵn, đang để placeholder — file này bị gitignore, không
bao giờ commit):

```bash
DEEPSEEK_API_KEY=sk-or-...      # key OpenRouter
JINA_API_KEY=jina_...           # key Jina
```

Ba dòng quan trọng khác đã set sẵn, **đừng đổi trước buổi demo**:

- `ANALYSIS_MODE=single` — mỗi lần phân tích chỉ gọi LLM một lần, hợp rate limit free. Chế độ `graph`
  gọi nhiều lần **và hiện chưa chạy known-issue matching**, sẽ mất một bước trong kịch bản.
- `DEMO_LOGS=true` — nút "Search logs" lấy log giả lập thay vì gọi CloudWatch.
- `EMBEDDING_DIM=768` — khớp với model Jina; migration tự resize cột vector khi khởi động.

---

## 2. Khởi động

```bash
# Bật Docker Desktop trước, rồi từ thư mục gốc repo:
docker compose up --build
```

Chờ tới khi log backend hiện `Application startup complete`, rồi mở **http://localhost:5173**.

Kiểm tra nhanh trước khi lên demo:

```bash
curl localhost:8000/healthz     # {"status":"ok","app":"IIM","database":"up"}
```

Muốn chạy test suite trong lúc stack đang lên, **phải truyền đúng số chiều embedding**, nếu không 5
test sẽ fail vì DB đã được migrate sang 768 chiều theo `.env` demo:

```bash
cd backend && EMBEDDING_DIM=768 uv run pytest     # 102 passed
```

Nếu góc trên bên phải UI hiện pill health màu xanh là stack đã thông.

---

## 3. Kịch bản demo

### Bước 1 — Đăng nhập (10 giây)

Nhập email bất kỳ → **Sign in**.

> Nói: "Phần đăng nhập hiện là gate tạm ở frontend, SSO thật nằm ở milestone sau."

Nói trước như vậy tốt hơn để người xem hỏi thì mình đã chủ động.

### Bước 2 — Nạp knowledge base (1 phút)

Vào **Knowledge Base** → **New document**. Tạo 2 tài liệu để RAG có thứ để trích dẫn:

**Tài liệu 1** — Title: `Runbook: payment-service OOM`, Service: `payment-service`,
Tags: `runbook, oom`, Content:

```
Khi payment-service bị OOMKilled: kiểm tra memory limit của task (hiện 512 MiB) và heap size của JVM.
Sự cố tháng 5/2026 do release tăng batch size lên 5000 record trong khi heap không đổi.
Cách xử lý: rollback về version trước, sau đó tăng task memory lên 1024 MiB rồi deploy lại.
Không tăng số task — vấn đề nằm ở memory từng task, scale ngang không giải quyết.
```

**Tài liệu 2** — Title: `Postmortem: cache regression r2026.07`, Service: `gcm-search-gateway`,
Tags: `postmortem, cost`, Content:

```
Release r2026.07.2 thay đổi cache layer khiến cache hit rate rớt từ 88% xuống ~30%,
kéo theo số lần gọi API bên thứ ba vượt budget ngày. Nguyên nhân: cache key thiếu tham số locale
nên mọi request đều miss. Fix: thêm lại locale vào cache key, hit rate trở về mức nền sau 2 giờ.
```

> Nói: "Đây là runbook và postmortem nội bộ — thứ mà on-call bình thường phải tự nhớ ra là có tồn tại."

### Bước 3 — Tạo incident và xem AI phân tích live (2 phút)

Vào **Incidents** → **New incident** → chọn sample **Infra OOM** (đã điền sẵn) → **Create**.

Chỉ ngay vào panel tiến trình: các stage chạy real-time qua SSE (retrieve → analyze → …), không phải
spinner giả.

Khi xong, chỉ vào kết quả:

- **Severity + summary** — mức độ và tóm tắt
- **Root cause** — chú ý nó nối `recent_deploy v2.14.0` (3 phút trước sự cố) với `memory_pct 99%`
- **Recommended action**
- **Evidence** — trích dẫn đúng chunk từ runbook vừa nạp ở bước 2

> Nói: "Nó không đoán. Root cause chỉ được rút ra từ context được cấp và bằng chứng lấy từ knowledge
> base — mỗi kết luận đều truy ngược được về nguồn."

### Bước 4 — Kéo log về và phân tích lại (2 phút)

Trong incident detail, tìm card **Search logs**:

- **Log group**: gõ `/ecs/payment-service-worker-oom`
- Start/End để nguyên (mặc định ±30 phút quanh thời điểm incident)
- Bấm **Search logs**

Log lines hiện ra, incident được phân tích lại trên chính đống log đó.

**Bấm Search logs lần nữa với đúng tham số cũ** → kết quả trả về tức thì, gắn nhãn cache **HIT
(0 tokens)**.

> Nói: "Cùng một sự cố lặp lại thì không tốn thêm một token nào. Nhưng nếu deploy version đổi,
> fingerprint đổi theo và hệ thống buộc phải phân tích lại — cache cũ không được phép chẩn đoán sai
> một sự cố sau deploy."

Tên log group quyết định kịch bản log giả lập:

| Log group chứa | Kịch bản |
|---|---|
| `oom`, `mem`, `worker` | OOMKilled, heap exhausted, task bị dừng |
| `alb`, `5xx`, `api`, `gateway` | 502/504, health check fail, connection pool cạn |
| còn lại | lỗi ứng dụng chung (traceback, DB timeout) |

### Bước 5 — Resolve và known-issue matching (2 phút)

Ở incident đang mở, bấm **Resolve**, điền cách xử lý:

```
Rollback về v2.13.4 và tăng task memory lên 1024 MiB. Sự cố dừng sau 4 phút.
```

Giờ tạo incident thứ hai: **New incident** → sample **Infra OOM** → **sửa `recent_deploy.version`
thành `v2.14.1`** → Create.

> Việc đổi version là bắt buộc: fingerprint gồm cả deploy version, giữ nguyên `v2.14.0` sẽ trúng cache
> và bỏ qua bước matching.

Incident mới hiện **banner "Known issue"** kèm % tương đồng và link sang incident đã xử lý.

> Nói: "On-call không phải chẩn đoán lại từ đầu. Hệ thống nhận ra ca này giống một ca đã fix, và chỉ
> thẳng sang cách đã xử lý lần trước."

### Bước 6 — Tạo ticket (30 giây, tùy chọn)

Nút **Create ADO ticket** chỉ hiện khi incident **chưa** được đánh dấu known issue và chưa có ticket —
đúng chủ ý: chỉ mở ticket cho lỗi thật sự mới.

Cần `AZDO_ORG` / `AZDO_PROJECT` / `AZDO_PAT` trong `.env`. **Không có credential thì bỏ qua bước này** —
nói bằng lời thay vì bấm và để nó lỗi trên màn hình.

### Bước 7 — Báo cáo ngày (1 phút)

Vào **Reports**, chọn ngày hôm nay. Digest tự sinh: số lượng theo severity + narrative.
Bấm **Copy for Slack**.

> Nói: "Cuối ca trực, dán thẳng vào kênh status. Không phải ngồi tổng hợp tay."

---

## 4. Khi có sự cố lúc demo

| Triệu chứng | Nguyên nhân thường gặp | Xử lý |
|---|---|---|
| UI hiện "can't reach the backend" | backend chưa lên xong | chờ hết migration, `docker compose logs backend` |
| Phân tích lỗi 429 / rate limit | OpenRouter free tier siết | đổi `DEEPSEEK_MODEL` sang model `:free` khác, restart backend |
| Phân tích lỗi 401 | key sai hoặc chưa được truyền vào container | `docker compose config` xem `DEEPSEEK_API_KEY` đã resolve chưa |
| Evidence rỗng | chưa nạp knowledge doc, hoặc `JINA_API_KEY` sai | làm lại bước 2 |
| Search logs lỗi NotImplementedError | `DEMO_LOGS` chưa `true` trong container | sửa `.env` rồi `docker compose up -d --force-recreate backend` |
| Muốn diễn lại từ đầu | dữ liệu cũ còn trong DB | `docker compose down -v && docker compose up --build` |

**Phương án dự phòng:** chạy thử trọn kịch bản một lượt trước buổi demo và **để nguyên dữ liệu đó
trong DB**. Nếu lúc demo mạng hoặc API free tier chết, vẫn còn incident đã phân tích sẵn để trình bày.
Quay màn hình một lượt chạy thành công cũng đáng, phòng khi mất mạng hoàn toàn.

---

## 5. Giới hạn — nên nói trước

Chủ động nêu, đừng để bị hỏi vặn:

- **Chưa có auto-ingest.** Alarm chưa tự tạo incident; SRE vẫn tạo tay. Đây là chủ đích — cần các team
  chủ quản cấp quyền trước.
- **Auth là gate tạm ở frontend.** Backend chưa xác thực.
- **Log trong demo là giả lập.** Adapter CloudWatch thật đã có và chạy được với SSO profile, chỉ là
  demo này không dùng tới.
- **Form log search chưa gửi bộ lọc theo error message.** API đã hỗ trợ `filter_pattern`, UI thì chưa
  nối — vẫn gọi được qua API trực tiếp.
- **Chỉ chạy local.** `iac/` còn rỗng, chưa deploy cloud.

---

## 6. Đổi sang provider khác

Adapter `deepseek` thực chất là client OpenAI-compatible, base URL cấu hình được — nên đổi provider chỉ
là đổi env, không sửa code:

```bash
# Groq
DEEPSEEK_BASE_URL=https://api.groq.com/openai/v1
DEEPSEEK_MODEL=llama-3.3-70b-versatile

# Ollama chạy local (không cần key, chậm hơn)
DEEPSEEK_BASE_URL=http://host.docker.internal:11434/v1
DEEPSEEK_MODEL=llama3.1

# Bedrock (mất phí, cần quyền AWS)
LLM_PROVIDER=bedrock
EMBEDDING_PROVIDER=titan
EMBEDDING_DIM=1024      # đổi dim cần tạo lại DB: docker compose down -v
```
