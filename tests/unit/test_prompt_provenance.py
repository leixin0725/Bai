"""[2026-07-20] 来源校验使用 pointer/span 和真实引用，不按重复正文反向猜测。"""

import pytest

from bai_agent.domain.errors import TraceIntegrityError
from bai_agent.domain.models import Participation, RequestPart, SourceKind, SourceRef
from bai_agent.model_calls.provenance import resolve_json_pointer, validate_provenance
from tests.prompt_debug_fakes import make_draft


def test_pointer_span_and_large_ordered_aggregate() -> None:
    payload = {"messages": [{"content": "重复重复"}]}
    refs = tuple(SourceRef(source_kind=SourceKind.RUNTIME, source_id=f"r-{i}", entity_ids=(f"rec-{i}",), producer="selector") for i in range(300))
    part = RequestPart(part_id="p", order=0, participation=Participation.INCLUDED, trust="untrusted_data", payload_pointer="/messages/0/content", text_span=(0, 2), content="重复", sources=refs)
    assert resolve_json_pointer(payload, "/messages/0/content") == "重复重复"
    validate_provenance(payload, (part,))
    assert part.sources[0].source_id == "r-0" and part.sources[-1].source_id == "r-299"


def test_unknown_source_and_bad_span_block_send() -> None:
    draft = make_draft("正文")
    bad = draft.parts[0].model_copy(update={"text_span": (1, 3)})
    with pytest.raises(TraceIntegrityError):
        validate_provenance({"messages": [{"content": "正文"}]}, (bad,))
