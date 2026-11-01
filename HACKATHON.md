

**Team name:** Git Over It

**Team members:**
- Khushi Jain
- Shreyansh Agrawal
- Abhiman Singh
- Ansh Pawar

**Selected problem statement:** Problem 01 – Intelligent Git Conflict Resolution with Testing Provenance


## AI models used

### Models used in the product (open-weight)

| Model | Access | Role in the product |
|---|---|---|
| **openai/gpt-oss-120b** (open-weight, Apache-2.0) | Groq API | Main model for demos and examples. Explains what each side of a conflict is trying to do and suggests a resolution, based on the provenance data (origin commit, tested / production status). |
| **openai/gpt-oss-20b** (open-weight, Apache-2.0) | Groq API | Same role during development and testing. Faster, and keeps the main model's rate limit free for the demo. |
| **Qwen2.5-Coder 7B** (open-weight) | Ollama, running locally | Offline fallback when the hosted API or network is unavailable. |

**Why these models:**
- **Open-weight**, as the hackathon rules require for models that do meaningful work in the product.
- **gpt-oss-120b:** a production-tier model on Groq. Fast responses, a 131K-token context window so whole conflict chunks fit, and JSON-mode support for structured output.
- **gpt-oss-20b:** the same model family and output format at lower latency and cost. It works for day-to-day development.
- **Qwen2.5-Coder 7B:** a strong code-focused model small enough to run on our laptops, so the tool still works without internet.

