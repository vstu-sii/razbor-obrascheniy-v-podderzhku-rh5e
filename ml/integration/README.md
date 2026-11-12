# AI endpoint integration reference

ai_service.py is the exact AI entry-point source used with Delivery PR39.
It depends on the Delivery queue/main/bootstrap modules and is not a standalone
server. The AI teacher PR does not overwrite another role's backend files.
The reviewed DEVELOPMENT integration additionally switches only cascade's
build_engine KB path to ml/data/cascade/kb.jsonl. Worker/migrations are unchanged.

Cache scope is loaded from persisted actor_ref after authenticated single-use
claim and pinned snapshot checks. Missing identity disables cache. Cache hits
keep the reservation boundary, report not_sent=true and do not emit a new LLM
generation. Deployment of the combined runtime belongs to Delivery.
