from argparse import Namespace

import pytest

from scripts.evaluate_research import main


@pytest.mark.parametrize('overrides', [
    {'live': True},
    {'external_results': 'private-reports'},
    {'cases': 'private-cases.json'},
])
async def test_failure_body_logging_rejects_non_fixture_data(overrides):
    options = {'debug_fixed_failures': True, 'live': False,
               'external_results': None, 'cases': 'eval/research_quality_cases.json'}
    options.update(overrides)
    # Rejection must happen before opening user data or starting any model run.
    with pytest.raises(ValueError, match='bundled synthetic fixed cases'):
        await main(Namespace(**options))
