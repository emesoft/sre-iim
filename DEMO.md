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
| `oom`, `mem` | OOMKilled exit 137, heap cạn, GC pause, task bị dừng |
| `alb`, `5xx`, `gateway`, `http` | 502/504, health check fail, connection pool cạn |
| `db`, `postgres`, `sql`, `rds` | pool bão hoà, deadlock, slow query, replica lag |
| `disk`, `volume`, `storage` | No space left on device, WAL panic, logrotate fail |
| `cert`, `tls`, `ssl` | certificate expired, handshake failure |
| `queue`, `kafka`, `sqs`, `backlog` | consumer lag, rebalance liên tục, DLQ phình |
| `quota`, `ratelimit`, `vendor`, `cost` | 429 từ vendor, vượt budget ngày, cache hit rate sập |
| còn lại | lỗi ứng dụng chung (traceback, DB timeout, worker chết) |

Ví dụ dùng được ngay: `/ecs/payment-service-worker-oom`, `/ecs/gcm-postgres-db`,
`/ecs/gcm-kafka-queue`, `/ecs/gcm-vendor-quota`, `/aws/alb/gcm-public-5xx`.

Mỗi dòng log có dạng `component  key=value ...` rồi tới nội dung, kèm level riêng — nên phân tích
trích được số cụ thể (`pool=100/100 waiting=64`, `exit_code=137`, `lag=184320`) thay vì chỉ mô tả chung.

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
| Phân tích lỗi 429 / rate limit | OpenRouter free tier siết | thử lại, hoặc đổi `DEEPSEEK_MODEL` sang model `:free` khác rồi restart backend |
| Incident ra `status: failed`, không rõ lý do | backend **không ghi log** lý do — chỉ đẩy qua SSE | gắn vào stream để thấy: `curl -N localhost:8000/api/incidents/{id}/stream` |
| Model trả 404 "unavailable for free" | model đó đã bị OpenRouter chuyển sang trả phí | liệt kê model free hiện tại (xem mục 6), đổi `DEEPSEEK_MODEL` |
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

- **Auto-ingest từ CloudWatch alarm đã có** (xem mục 7) nhưng cần cấu hình AWS connection trước —
  mặc định demo không bật vì cần tài khoản AWS thật.
- **Auth trang chính là gate tạm ở frontend** (bất kỳ email/password nào cũng vào được). Riêng trang
  **Settings** đã có xác thực thật (1 password admin dùng chung, xem mục 7) — hai cơ chế độc lập
  nhau, đừng nhầm.
- **Log trong demo là giả lập.** Adapter CloudWatch thật đã có và chạy được với SSO profile, chỉ là
  demo này không dùng tới.
- **Form log search chưa gửi bộ lọc theo error message.** API đã hỗ trợ `filter_pattern`, UI thì chưa
  nối — vẫn gọi được qua API trực tiếp.
- **Chỉ chạy local.** `iac/` còn rỗng, chưa deploy cloud.

---

## 6. Auto-ingest từ CloudWatch alarm (tùy chọn, cần tài khoản AWS thật)

Ngoài việc tạo incident bằng tay, app có thể **tự động** poll CloudWatch Alarms mỗi 60 phút (hoặc
bấm "Refresh now" để poll ngay) và tự tạo/tự resolve incident theo trạng thái alarm. Không bắt buộc
cho demo cơ bản (mục 1–6 ở trên không cần bước này), chỉ cần khi muốn trình bày luồng SRE thật với
alarm thật.

### 6.1. Điền thêm vào `.env`

```bash
# Mã hóa access key AWS lưu trong DB (bắt buộc để dùng tính năng AWS connection)
SECRET_ENCRYPTION_KEY=...   # sinh bằng: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Password admin để vào trang Settings (nơi thêm AWS connection) — 1 password dùng chung, không phải per-user
ADMIN_PASSWORD=...          # tự chọn
ADMIN_JWT_SECRET=...        # sinh bằng: python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Thiếu `SECRET_ENCRYPTION_KEY` → lưu AWS connection báo lỗi 500. Thiếu `ADMIN_PASSWORD`/
`ADMIN_JWT_SECRET` → trang Settings báo lỗi 503 (server chưa cấu hình), không phải "sai mật khẩu".

Sau khi sửa `.env`, rebuild lại cả 2 service (Dockerfile backend đã cài sẵn Node.js/CLI cho
provider `claude_cli`, xem mục 8):

```bash
docker compose up -d --build backend frontend
```

### 6.2. Chuẩn bị quyền AWS (chọn 1 trong 2 cách)

**Cách A — SSO profile (khuyến nghị nếu công ty đã dùng AWS IAM Identity Center):**

Cấu hình sẵn trong `~/.aws/config` **trên máy chạy `docker compose`** (không phải trong app) —
ví dụ:

```ini
[sso-session my-sso]
sso_start_url = https://your-org.awsapps.com/start
sso_region = us-east-1
sso_registration_scopes = sso:account:access

[profile my-project-dev]
sso_session = my-sso
sso_account_id = 123456789012
sso_role_name = ReadOnlyAccess
region = us-east-1
output = json
```

Rồi đăng nhập (mở trình duyệt, cache token vào `~/.aws/sso/cache/` — container đọc read-only từ
đây, tự đăng nhập hộ được):

```bash
aws sso login --profile my-project-dev
```

Token có hạn (thường vài giờ tùy tổ chức) — hết hạn thì poll sẽ lỗi, chạy lại đúng lệnh trên để
làm mới, không cần sửa gì trong app.

**Cách B — Access key (đơn giản hơn, không cần cấu hình gì trước):** tạo 1 IAM user với quyền tối
thiểu `cloudwatch:DescribeAlarms` (policy `CloudWatchReadOnlyAccess` là đủ), tạo Access Key cho
user đó, dùng trực tiếp ở bước 7.3 — không cần bước nào ở máy host.

### 6.3. Thêm connection trong app

1. Vào **Settings** → nhập `ADMIN_PASSWORD` nếu được hỏi.
2. Điền form "Add connection": chọn Project (hoặc "Other..." để gõ tên tự do), Env, Region.
3. Chọn **SSO profile** (điền đúng tên profile, ví dụ `my-project-dev`) hoặc **Access key** (dán
   Access Key ID + Secret access key — được mã hóa trước khi lưu, không bao giờ hiển thị lại).
4. **Add connection** → bấm **Test** để xác nhận gọi CloudWatch được thật.
5. Bấm **Refresh now** để poll ngay, hoặc đợi tự động (60 phút/lần).

Alarm nào đang ở trạng thái **ALARM** tại thời điểm poll sẽ tự tạo 1 incident (xem ở trang
**Incidents**, nguồn `cloudwatch_alarm`); khi alarm về **OK**, incident tương ứng tự chuyển
**resolved**. Không có alarm nào đang kêu → poll xong không có gì mới, đây là hành vi đúng, không
phải lỗi.

## 7. Đổi sang provider khác

Adapter `deepseek` thực chất là client OpenAI-compatible, base URL cấu hình được — nên đổi provider chỉ
là đổi env, không sửa code:

Danh sách model free của OpenRouter đổi theo thời gian. Model đã bị rút khỏi free trả về **404
`This model is unavailable for free`** — không phải lỗi auth, nên key vẫn tốt. Liệt kê model free
đang sống:

```bash
curl -s https://openrouter.ai/api/v1/models | jq -r '.data[].id | select(endswith(":free"))'
```

`openai/gpt-oss-20b:free` là model đã được kiểm chứng chạy đúng với pipeline này (trả JSON sạch ở
`content`, phần reasoning tách riêng nên không làm hỏng parser).

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

**`claude_cli` — dùng subscription Claude Code cá nhân thay vì trả phí API (CHỈ demo local, không
public, không dùng cho sản phẩm thật — xem cảnh báo trong
`backend/app/infrastructure/llm/claude_cli.py`):**

```bash
LLM_PROVIDER=claude_cli
CLAUDE_CLI_MODEL=sonnet    # hoặc opus/haiku
```

Setup: chạy `claude setup-token` trên máy chạy `docker compose` (mở trình duyệt đăng nhập Claude
Code, in ra 1 token) → dán token đó vào app, ở **Settings** → mục "Claude Code token" (không phải
`.env`) → Save. Cần `SECRET_ENCRYPTION_KEY` đã cấu hình (mục 7.1) để lưu token này.
