"""Typed program-owned verification boundary; suppliers are never called here."""
import pytest
from backend.domain.models import TargetVerification
from backend.research.graph import bind_target_verification


def test_duplicate_program_claim_indices_fail_before_binding():
    verdict=TargetVerification(checks=[{'index':0,'atomic_checks':[{'target_id':'0:0','supported':True}]}])
    with pytest.raises(ValueError,match='duplicate'):
        bind_target_verification(verdict,[{'index':0,'text':'旧声明。'},{'index':0,'text':'新声明。'}])


def cached(supported=True):
    from backend.research.verification_contract import CURRENT_VERIFICATION
    verdict=bind_target_verification(TargetVerification(checks=[{'index':0,'supported':supported,
        'atomic_checks':[{'target_id':'0:0','supported':supported}]}]),[{'index':0,'text':'完整原文。'}])
    return {'contract_version':CURRENT_VERIFICATION.contract_version,'wire_version':CURRENT_VERIFICATION.wire_version,
            'verdict':verdict.model_dump(),'input':{'claims':[{'index':0,'text':'完整原文。'}]}}


@pytest.mark.parametrize('corruption',['old_version','unknown_target','different_text','duplicate_input','malformed'])
def test_cached_verdict_cannot_bypass_typed_binding(corruption):
    from backend.research.verification_contract import CURRENT_VERIFICATION
    saved=cached()
    if corruption=='old_version':
        saved['wire_version']='obsolete'
    elif corruption=='unknown_target':
        saved['verdict']['checks'][0]['atomic_checks'][0]['target_id']='other:0'
    elif corruption=='different_text':
        saved['verdict']['checks'][0]['atomic_checks'][0]['text']='来源里的另一个事实。'
    elif corruption=='duplicate_input':
        saved['input']['claims']*=2
    else:
        saved='malformed-json'
    assert CURRENT_VERIFICATION.load_saved(saved,{0:'完整原文。'}) is None


def test_semantic_false_is_complete_and_reusable_without_becoming_supported():
    from backend.research.verification_contract import CURRENT_VERIFICATION
    result=CURRENT_VERIFICATION.load_saved(cached(False),{0:'完整原文。'})
    assert result and result.checks[0].verification_complete and not result.checks[0].supported


def test_partial_evidence_keeps_checked_claim_without_reusing_incomplete_cache():
    from backend.research.verification_contract import CURRENT_VERIFICATION, supported_indices
    claims=[{'index':0,'text':'已核验事实。'},{'index':1,'text':'尚未核验。'}]
    verdict=bind_target_verification(TargetVerification(checks=[{'index':0,
        'atomic_checks':[{'target_id':'0:0','supported':True}]}]),claims)
    saved={'contract_version':CURRENT_VERIFICATION.contract_version,'wire_version':CURRENT_VERIFICATION.wire_version,
           'input':{'claims':claims},'verdict':verdict.model_dump()}
    texts={claim['index']:claim['text'] for claim in claims}
    assert CURRENT_VERIFICATION.load_saved(saved,texts) is None
    partial=CURRENT_VERIFICATION.load_saved(saved,texts,allow_partial=True)
    assert supported_indices(partial,{0,1},texts)=={0}
