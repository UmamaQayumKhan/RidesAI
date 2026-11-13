# Interview Guide: How to Present AgentDesk

## 1. One-minute pitch

> "I built AgentDesk, a mini SaaS platform where a business connects an AI agent to WhatsApp. The agent works on two sides. On the customer side it chats on WhatsApp and places orders. On the business side the owner manages the dashboard by talking to an AI assistant. The agent answers product, price and stock questions from the business's real database, collects order details, shows an order summary, and only places the order after the customer says yes. Orders appear on a dashboard where the business manages products, orders, customers and conversations. It's built with FastAPI, the OpenAI API with tool calling, SQLAlchemy and Jinja2. It has a real Meta WhatsApp Cloud API integration with signature verification and duplicate protection, plus a simulator so it can be demoed without Meta credentials."

**Tie it to Rides Technologies:**

> "I named the agent **RidesAI** because this is the kind of 24/7 AI chatbot your RidesAI platform offers. The same idea works for the industries you serve, like restaurants, pharmacies and clothing stores. Only the product catalog and the agent settings change."

---

## 2. Five-minute live demo script

1. **Log in** (`demo@example.com` / `demo12345`) and show the **Dashboard**: KPI cards, revenue chart, orders by status, low stock.
2. **Products:** "The AI can only sell what is here, at these prices." Point out the out-of-stock speaker.
3. **WhatsApp Simulator:** "This uses the exact same pipeline as real WhatsApp, but nothing goes to Meta."
   - `Hi, do you have earbuds?` → the agent calls `search_products`.
   - `I want 2. My name is Umama Qayum Khan, address House 5, Street 10, G-11/2, Islamabad` → the agent calls `prepare_order` and shows a summary. "The total comes from the database, not the model."
   - `yes` → the agent calls `confirm_order`, and the order number is returned.
4. **Orders:** the new order is there. Open it, then change the status to *confirmed*. "If I cancel, stock comes back."
5. **AI Business Assistant (the agent manages the dashboard):** open **AI Assistant**.
   - `How did we do in the last 7 days?` → a real report from the database.
   - `Mark order 6 as shipped` → a green action chip appears. Open **Orders**: it shows *shipped*. Open the **Simulator**: the customer automatically got "Your order has been shipped 🚚".
   - `Add 20 units to the phone case` → the stock changes on the Products page.
   - Say: "Same tool-calling design as the customer agent, but with owner tools. Every change is validated, limited to this business, and shown to the owner."
6. **Hand-off:** in the simulator, type `I want to speak to a human`. Open **Conversations**: it shows *Human needed*. Reply as the team, and the reply appears in the simulator. Click **Hand back to AI**.
7. **Agent Settings:** change the instructions and business info. Show the WhatsApp credentials form (secrets masked) and the webhook URL.
8. **Code:** open `services/tools.py` (tools and the confirmation guards) and `services/agent.py` (the tool-calling loop). Run `pytest` and show that all 37 tests pass.

---

## 3. Step by step: what I built and why (simple language)

**Step 1: Settings (`config.py`).** All secrets and options come from environment variables, loaded with `pydantic-settings`. `.env.example` shows what is needed. Nothing secret is hard-coded.

**Step 2: Database (`database.py`, `models.py`).** I used SQLAlchemy 2.0 with SQLite. Because everything goes through SQLAlchemy, switching to PostgreSQL is just a new `DATABASE_URL`. Tables: businesses, agent_settings, whatsapp_configs, products, customers, conversations, messages, orders, order_items.
- Every business-owned table has a `business_id`, which gives **multi-tenant isolation**.
- Money is stored as **integer cents**, so there are no float rounding bugs.
- `order_items` **copies** the product name and price. If the product changes later, old orders stay correct.

**Step 3: Security (`security.py`).**
- Passwords are hashed with PBKDF2-SHA256 and a random salt. It's built into Python, so there's nothing to compile on Windows.
- Signed session cookies, and the session is renewed at login (prevents session fixation).
- A **CSRF token** is required on every form and API write.
- WhatsApp tokens are **encrypted** with Fernet before saving.

**Step 4: Order logic (`services/orders.py`).** One place for business rules: validate items, check stock, price from the database, create the order, reduce stock, and handle status changes (cancel puts stock back). Both the AI and the dashboard use it, so the rules can't drift apart.

**Step 5: AI tools (`services/tools.py`).** Seven functions the LLM can call, each described with a JSON schema. The model chooses the tool and arguments; my code does the work and returns JSON. Tool errors are returned as JSON (not exceptions), so the model can recover. For example: "only 3 in stock" → the agent tells the customer.

**Step 6: The agent loop (`services/agent.py`).** System prompt (from the business's settings) + last 20 messages → call the LLM with tools → run any tool calls → send the results back → repeat until there's a text reply, with a maximum of 6 rounds. Hitting the limit hands off to a human. The LLM client is created through a factory, so tests can swap in a fake.

**Step 7: The shared pipeline (`services/conversation.py`).** One function handles a customer message for both demo and live: dedupe → save → (skip if a human is in control) → agent → save reply. Writing it once means the simulator really proves the live behaviour.

**Step 8: WhatsApp live mode (`services/whatsapp.py`, `routers/webhook.py`).**
- **GET** verification: compare `hub.verify_token` and return `hub.challenge`.
- **POST**: verify `X-Hub-Signature-256` (HMAC-SHA256 of the raw body with the App Secret). Respond 200 quickly, because Meta retries slow webhooks, and process the message in a background task.
- **Duplicates:** the WhatsApp message id is stored in a UNIQUE column. It is committed before calling the AI, so a retry is ignored.
- Each business has its own random `webhook_key` URL and its own credentials.

**Step 9: Demo mode (`routers/simulator.py`, `static/js/simulator.js`).** A WhatsApp-style chat page calling `/api/simulator/message`. It is clearly labelled DEMO, conversations are marked `channel=simulator`, and it never contacts Meta.

**Step 10: Dashboard UI (`templates/`, `static/`).** Jinja2 templates with a sidebar layout, KPI cards, tables and modals. Plain CSS, responsive down to mobile, with no frontend framework needed.

**Step 11: Automatic order updates (`services/notifications.py`).** One function changes the status, writes a message into the customer's chat, commits, then sends it on WhatsApp. The order is important: save first, then send, so a customer is never told about a change that failed. The dashboard, the API and the owner assistant all call it.

**Step 12: AI Business Assistant (`services/owner_assistant.py`, `services/owner_tools.py`).** A second agent for the owner. It reuses the same loop (`run_tool_loop`), with a different prompt and 9 owner tools: reports, list and update orders, find, edit and add products, low stock, chats needing a human. Every change is validated, scoped to the business, and returned as an "action" the UI shows. This covers "the agent manages the business dashboard".

**Step 13: Tests (`tests/`).** 37 pytest tests: auth, CSRF, data isolation, pricing from the database, the confirmation guards, stock on cancel, the full order flow with a scripted fake LLM, hand-off, and webhook verification, signatures, duplicates and non-text messages.

---

## 4. Likely interview questions and good answers

**Q: How do you stop the LLM from making up prices?**
The model never sends prices. `prepare_order` only accepts product IDs and quantities, and prices and totals are computed in Python from the database. The system prompt also tells it to always use `search_products`. Even if the model writes a wrong number in its text, the stored order uses database prices. There's a test that passes a fake price and checks it is ignored.

**Q: What if the model calls `confirm_order` without the customer agreeing?**
The server enforces it, not the prompt. `confirm_order` checks that the confirmation is a *later* customer message than the summary, and that the message is a clear yes. It also re-checks prices before creating the order.

**Q: How do you handle duplicate webhooks?**
Meta can deliver the same event more than once. The WhatsApp message id is saved in a UNIQUE column and committed **before** the AI runs. A second delivery either finds the id or fails on the unique constraint, and is ignored.

**Q: Why verify the signature?**
Anyone can POST to a public URL. The HMAC signature with the App Secret proves the request really came from Meta and the body wasn't changed. I use `hmac.compare_digest` to avoid timing attacks.

**Q: Why respond 200 immediately?**
The LLM can take several seconds. Meta expects a fast response and will retry otherwise, so the work happens in a background task. In production I'd use a proper queue (Celery/RQ) with retries.

**Q: How is data separated between businesses?**
Every table has `business_id`, and every query filters by the logged-in business, through helpers like `get_owned_product()` that return 404 otherwise. Tools receive the business from the context, never from the model. A test checks that business B can't read or edit business A's products.

**Q: How does conversation memory work?**
The last N messages (default 20) are loaded from the database on every turn. The pending order summary and known customer details are put in the system prompt, so important state survives even if old messages drop out of the window.

**Q: Why SQLite? Will it scale?**
It's perfect for an MVP and a local demo. Everything uses SQLAlchemy, so production can move to PostgreSQL by changing `DATABASE_URL`, with Alembic for migrations.

**Q: What happens if OpenAI is down?**
The error is caught and logged. The customer gets a polite message, and the chat is marked "human needed" so staff can follow up. No crash and no silent failure.

**Q: Why not use a framework like LangChain?**
The flow is simple enough to write directly with the OpenAI SDK, which keeps it transparent and easy to test, with fewer dependencies. For more complex multi-agent flows I'd consider LangGraph or the OpenAI Agents SDK.

**Q: How does the agent "manage the dashboard"?**
In two ways. Automatically: orders, customers, conversations and stock changes created by the customer agent appear on the dashboard by themselves, and status changes trigger customer messages. On request: the owner chats with the AI Business Assistant, which reports, updates orders and manages products through owner-only tools.

**Q: Isn't it risky to let an AI change prices and stock?**
The AI can only call specific tools, never raw SQL. Each tool validates input (no negative stock, valid prices), only touches the logged-in business, and returns exactly what changed, which the UI shows as an action chip. Only the logged-in owner can use it, protected by session and CSRF. A next step would be asking for a confirmation click before bigger changes, like price updates.

**Q: Why two agents instead of one?**
Different users need different permissions. Customers must never be able to change prices or see other orders, and the owner needs management tools. Separate tool sets mean a customer can't trick the agent into calling an owner tool, because the tool simply isn't available in that chat.

**Q: What would you add next?**
A job queue, rate limiting, Alembic, WhatsApp templates for the 24-hour window, delivery status tracking, RAG over business documents (FAQs and policies) with embeddings, analytics on AI resolution rate, and Docker deployment.

---

## 5. Known limitations (be honest about them)

- Background tasks run in the web process. A server restart during processing can drop a reply (use a queue in production).
- Only text messages are understood. Images and voice notes get a fixed reply.
- No WhatsApp template messages, so the business can't message a customer first after 24 hours.
- The confirmation keyword check is simple. It's a safety net on top of the model's judgement, not full NLU.
