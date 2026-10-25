"""Time build_crypto_import over a long synthetic account history (AA50).

Run from the clone's root, on a quiet machine, before and after a change:

    sh agents/lib/run.sh -m scripts.bench_crypto_import [N] [REPEAT]

N buys spread over the five years before the filing year and N sells, a tenth
of them in the filing year (TY2025), all in one account: years of history
dominate, as they do for an active filer. Each buy leaves half a coin behind,
so the FIFO pool keeps growing. Synthetic data only; nothing is written. Prints
the median and minimum of REPEAT whole imports, after one untimed run.
"""
import copy
import statistics
import sys
import time

from agents.lib import crypto_importer as C


def history(n: int) -> list:
    tx = []
    for i in range(n):
        tx.append(dict(account_id='A', txn_id=f'b{i}', kind='buy',
                       date=f'{2020 + i % 5}-01-{1 + i % 28:02d}T10:00:00Z', asset='BTC',
                       quantity='1', usd_amount=100 + i % 50, fee_usd='0.25'))
    for i in range(n):
        year = 2025 if i % 10 == 0 else 2021 + i % 4
        tx.append(dict(account_id='A', txn_id=f's{i}', kind='sell',
                       date=f'{year}-06-{1 + i % 28:02d}T12:00:00Z', asset='BTC',
                       quantity='0.5', usd_amount=90 + i % 70, fee_usd='0.10'))
    return tx


def main(argv: list) -> None:
    n = int(argv[0]) if argv else 20000
    repeat = int(argv[1]) if len(argv) > 1 else 5
    tx = history(n)
    block = dict(accounts={'A': dict(forms_received=False, evidence='statement')}, forms=[], resolutions={})

    def run() -> dict:
        return C.build_crypto_import(copy.deepcopy(tx), tax_year=2025, broker_reporting=copy.deepcopy(block),
                                     specific_identification=False, account_aware=True)

    result = run()
    assert result['broker_reporting']['complete'], result['broker_reporting']['unresolved'][:2]
    times = []
    for _ in range(repeat):
        start = time.perf_counter()
        run()
        times.append(time.perf_counter() - start)
    form = result['form_8949']
    print(f'N={n} transactions={len(tx)} rows={len(form["part_i_short_term"]) + len(form["part_ii_long_term"])}')
    print(f'whole import median {statistics.median(times):.3f}s  min {min(times):.3f}s over {repeat} runs')


if __name__ == '__main__':
    main(sys.argv[1:])
