"""Narrow LAB04 instruction-override policy, preserved from integrated baseline."""
import re
import unicodedata


def unsupported_reissue_policy(card):
    """Block a narrow invented reissue prohibition; this is not semantic entailment."""
    fields = [card.get('draft_reply') or '', card.get('summary') or '',
              card.get('next_step') or '', *card.get('known_facts', [])]
    patterns = (
        r'не\s+(?:предоставля\w*|выда\w*|восстанавлива\w*).{0,45}повторно',
        r'повторн\w*.{0,45}не\s+(?:предоставля\w*|выда\w*|восстанавлива\w*)',
        r'не\s+может.{0,30}(?:выдан|предоставлен|восстановлен).{0,30}повторно',
    )
    normalize = lambda text: unicodedata.normalize('NFKC', text).casefold()
    claimed = any(re.search(pattern, normalize(text)) for text in fields for pattern in patterns)
    # The provenance gate has already verified these exact approved excerpts.
    supported = any(re.search(pattern, normalize(item.get('excerpt', '')))
                    for item in card.get('evidence', []) for pattern in patterns)
    return 'unsupported_reissue_policy' if claimed and not supported else None

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
