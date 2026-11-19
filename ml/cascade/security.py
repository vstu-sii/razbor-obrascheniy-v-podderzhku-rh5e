"""Narrow LAB04 instruction-override policy, preserved from integrated baseline."""
import re
import unicodedata

def untrusted_instruction_reason(request, sources):
    """Conservatively route explicit instruction overrides to a human.

    This is a narrow input policy, not a proof of prompt-injection immunity or
    semantic grounding. Legitimate quoted instructions may need manual review.
    """
    values = [request['ticket_text']] + [item['text'] for item in request.get('history', [])]
    values += [item['title'] + '\n' + item['excerpt'] for item in sources]
    patterns = (
        r'игнорир(?:уй|уйте).{0,100}(?:правил|инструкц|запрет)',
        r'ignore.{0,80}(?:previous|prior|above|system|rules|instructions)',
        r'(?:^|\n)\s*(?:system|developer|системное сообщение)\s*:',
        r'(?:установи|set|верни|return).{0,80}needs_human_review.{0,20}false',
        r'(?:напиши|write|выведи|output).{0,80}draft_reply',
        r'<\|(?:system|im_start|developer)\|>',
    )
    for value in values:
        normalized = unicodedata.normalize('NFKC', value).casefold()
        if any(re.search(pattern, normalized) for pattern in patterns):
            return 'untrusted_instruction_manual_review'
    return None
