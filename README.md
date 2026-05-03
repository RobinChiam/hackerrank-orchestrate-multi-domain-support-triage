# 🛡️ Support Triage Agent: HackerRank Orchestrate Hackathon

> **Date:** May 1st, 2026 | **Event:** HackerRank Orchestrate Hackathon
>
> A highly efficient, deterministic, and locally-grounded AI Support Triage Agent built for my first-ever hackathon!

---

## 📖 The Challenge

During the hackathon, we were tasked with building a Support Triage Agent capable of handling incoming customer issues across multiple diverse domains: **Claude, Visa, and HackerRank**. 

The constraints were strict:
- We were provided a local-only support corpus (calculated to be 774 files).
- We needed to implement **Retrieval-Augmented Generation (RAG)** principles to triage incoming support issues.
- The agent must return strictly **grounded responses** or seamlessly **escalate** to a human agent if it cannot find sufficient evidence.
- **Zero hallucinations, no guessing, and absolutely no assisting with out-of-scope issues.**

## 🧩 Problems & Core Considerations

To win this challenge, I had to solve three primary problems:

1. **⚡ Speed and Efficiency:** How can I make the application run blazingly fast while minimizing token usage to avoid accidental and exorbitant API costs?
2. **⚓ Response Grounding:** How do I ensure that responses stay anchored to the provided text, and how can the AI reliably self-identify when an issue exceeds its knowledge boundaries?
3. **🎯 Determinism and Seed Sampling:** How do I guarantee that the outcomes are predictable and do not deviate arbitrarily between identical runs?

---

## 🛠️ Tech Stack & Tooling

We were granted the freedom to use any tools, including AI assistants. Here is my carefully selected stack:

- **Development Tool:** **Codex (GPT-5.4)**. I utilized Codex to rapidly build the application, ensuring my prompts were clear and concise. The resulting codebase strictly uses standard Python libraries without bloat or unnecessary third-party integrations, relying only on the direct API calls to my models.
- **Language:** **Python**. Starting from an empty `main.py`, Python allowed for incredibly fast build times (crucial for a 24-hour window) and enabled seamless integration with direct REST API calls.
- **RAG System:** **Semantic Search via Vector Embeddings**. This was chosen over keyword-based searches (like BM25) to ensure that arbitrary, natural language requests could still be semantically matched against the local-only support corpus, preventing any reliance on web calls.
- **AI Models:** I leveraged a dual-model approach utilizing Google's cutting-edge Gemini ecosystem:
  - **Embedding:** `gemini-embedding-2`. Ideal for asymmetrical search tasks (where the query and documentation formats differ) and capable of rich multidimensional embeddings.
  - **Triage & Reasoning:** `gemini-3.1-flash-lite-preview`. A newly released, incredibly fast model that balances high reasoning capabilities with low token costs, optimizing our Time to Response (TTR) and Time to Resolution.

---

## 🏗️ Architecture

The application is structured into three distinct, powerful layers:

### 1. Hardened Triage Layer
Incoming requests are fraught with potential risks, including legal issues, threats, or malicious prompt-injection attempts. 
- This layer performs rapid **sentiment analysis**, **risk-level assessment**, and **malicious intent detection**.
- Using a rule-based deterministic router, it instantly decides if a ticket should be safely escalated with a canned response, bypassing the reasoning layer entirely. This saves resources and protects the system.

### 2. Retrieval Layer
The `RetrievalEngine` parses the extensive support corpus, cleans the markdown, and chunks the text.
- These chunks are passed to `gemini-embedding-2` to create 768-dimensional vector embeddings.
- Embeddings are stored efficiently in a local **SQLite** database (`.sqlite3`). This setup is lightning-fast, infinitely scalable for thousands of documents, and requires zero external dependencies.
- Natural language queries are embedded and compared against the vector clusters using **Cosine Similarity**, allowing the system to instantly find the closest semantic matches without relying on exact keywords.

### 3. Reasoning Layer
Responses are intelligently generated using `gemini-3.1-flash-lite-preview`.
- The LLM assesses the context retrieved from the database and constructs a grounded reply.
- **Safety Checks:** Crucially, the model evaluates its own evidence via a strict boolean `grounded` flag. If it determines the retrieved information is insufficient to answer the query accurately, the application intercepts the generation and cleanly escalates the ticket to a human.

---

## 🎲 Handling Determinism & Predictability

LLMs inherently use probabilistic token sampling, meaning identical inputs can sometimes yield different outputs. To achieve rigorous determinism:

1. **Early Malicious Filtering:** In the initial triage phase, any detected malicious requests immediately trigger a hardcoded canned response. This prevents wasting API costs on attacks and guarantees a 100% predictable response for bad actors.
2. **Temperature Zero:** The reasoning layer operates at `temperature = 0.0`, forcing greedy decoding (selecting the most probable token every time).
3. **Seed Sampling:** We pass a constant seed parameter (`DEFAULT_SEED = 42`) to the Gemini API. By pinning the sampling seed alongside a zero temperature, we stabilize the underlying sampling engine, ensuring the AI's responses are predictable, reliable, and consistent across multiple evaluation runs.

---

## 🚀 Getting Started

Ensure you have Python 3.11+ installed. No third-party pip packages are required!

1. Add your API key to a `.env` file in the root directory:
   ```env
   GEMINI_API_KEY=your_api_key_here
   ```
2. Build the local vector index:
   ```bash
   python3 code/main.py index
   ```
3. Run the full triage batch job:
   ```bash
   python3 code/main.py run
   ```

*Built with ❤️ for the HackerRank Orchestrate Hackathon.*
