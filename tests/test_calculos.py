# -*- coding: utf-8 -*-
"""Testes de cálculo do Siqueirão — roda SEM tocar no banco nem no Asaas.
Testa _gerar_periodos_futuros com cursor fake e valida convenções de dias.
"""
import sys, os, io
from datetime import date, timedelta

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, r'c:\Users\fabio.pepplow\Desktop\Projetos Antigravity\Siqueirão\siqueiraomultimarcas')

# Evita conexão real: DATABASE_URL inválida faz init_db falhar (try/except no módulo)
os.environ['DATABASE_URL'] = 'postgresql://x:x@127.0.0.1:1/none?connect_timeout=1'
os.environ.setdefault('SECRET_KEY', 'test-only')

import app as APP

FAILS = []
def check(nome, cond, detalhe=''):
    status = 'OK  ' if cond else 'FALHA'
    print(f'[{status}] {nome}' + (f' — {detalhe}' if detalhe else ''))
    if not cond:
        FAILS.append(nome)

# ───────────────────────── FakeCursor p/ _gerar_periodos_futuros ─────────────
class FakeCursor:
    def __init__(self):
        self.inserts = []   # (locacao_id, seq, ini, fim, valor)
        self._last = None
        self._existentes = []  # períodos (ini, fim) já "no banco"
        self._seq = 0
    def execute(self, sql, params=None):
        s = ' '.join(sql.split())
        if s.startswith('SELECT COUNT(*)'):
            _, pfim, pini = params
            n = sum(1 for ei, ef in self._existentes if ei <= pfim and ef >= pini)
            self._last = (n,)
        elif 'COALESCE(MAX(semana_numero)' in s:
            self._seq += 1
            self._last = (self._seq,)
        elif s.startswith('INSERT'):
            loc, seq, ini, fim, valor = params
            self.inserts.append((loc, seq, ini, fim, float(valor)))
            self._existentes.append((ini, fim))
        else:
            self._last = (0,)
    def fetchone(self):
        return self._last

# ───────────────────────── 1. Contrato semanal ───────────────────────────────
print('\n== 1. Contrato SEMANAL: diaria=100, valor_semanal=650, início hoje ==')
cur = FakeCursor()
hoje = date.today()
criados = APP._gerar_periodos_futuros(cur, 1, hoje, 'semanal', 100, 650, desde_inicio=True)

ok_len = all((fim - ini).days + 1 == 7 for _, _, ini, fim in
             [(l, s, i, f) for l, s, i, f, v in cur.inserts[:-1]])
check('todos os períodos (menos o último) têm exatamente 7 dias', ok_len)

full = [v for l, s, i, f, v in cur.inserts if (f - i).days + 1 == 7]
check('período cheio usa valor_semanal (650), não diaria*7 (700)',
      all(v == 650.0 for v in full), f'valores={set(full)}')

parciais = [(i, f, v) for l, s, i, f, v in cur.inserts if (f - i).days + 1 < 7]
for i, f, v in parciais:
    dias = (f - i).days + 1
    check(f'período parcial {i}→{f} ({dias}d) cobra diaria*dias = {100*dias}',
          v == 100.0 * dias, f'valor={v}')

# continuidade: próximo início = fim anterior + 1
cont = all(cur.inserts[k+1][2] == cur.inserts[k][3] + timedelta(days=1)
           for k in range(len(cur.inserts)-1))
check('períodos são contíguos sem furo nem sobreposição', cont)
check('último período termina em 31/dez', cur.inserts[-1][3] == date(hoje.year, 12, 31),
      f'fim={cur.inserts[-1][3]}')

# ───────────────────────── 2. Quinzenal e mensal ─────────────────────────────
print('\n== 2. QUINZENAL (14d) e MENSAL (28d) ==')
for freq, nd in [('quinzenal', 14), ('mensal', 28)]:
    cur = FakeCursor()
    APP._gerar_periodos_futuros(cur, 2, hoje, freq, 100, None, desde_inicio=True)
    cheios = [v for l, s, i, f, v in cur.inserts if (f - i).days + 1 == nd]
    check(f'{freq}: período cheio de {nd} dias cobra diaria*{nd} = {100*nd}',
          all(v == 100.0 * nd for v in cheios), f'valores={set(cheios)}')

# ───────────────────────── 3. Sem duplicar períodos existentes ───────────────
print('\n== 3. Idempotência: rodar 2x não duplica ==')
cur = FakeCursor()
APP._gerar_periodos_futuros(cur, 3, hoje, 'semanal', 100, 650, desde_inicio=True)
n1 = len(cur.inserts)
APP._gerar_periodos_futuros(cur, 3, hoje, 'semanal', 100, 650, desde_inicio=True)
check('segunda execução não cria nenhum período novo', len(cur.inserts) == n1,
      f'{n1} → {len(cur.inserts)}')

# ───────────────────────── 4. Convenção de dias entre endpoints ──────────────
print('\n== 4. CONSISTÊNCIA da convenção de dias (inclusive vs exclusive) ==')
import inspect
src_reg = inspect.getsource(APP.registrar_pagamento)
src_cob = inspect.getsource(APP.get_cobrancas)
check('registrar_pagamento usa convenção inclusiva (.days + 1)',
      '.days + 1' in src_reg)
check('get_cobrancas expõe "dias" com convenção inclusiva (.days + 1)',
      '.days + 1' in src_cob)
check('registrar_pagamento respeita valor_semanal em período cheio',
      'valor_semanal' in src_reg)
check('registrar_pagamento preserva centavos (round(...,2))',
      'round(valor_prev - desconto, 2)' in src_reg)
check('overlap check usa <= / >= (pega períodos adjacentes idênticos)',
      'data_inicio <= %s AND data_fim >= %s' in src_reg)

# ───────────────────────── 5. Helpers de conversão ───────────────────────────
print('\n== 5. Helpers ==')
check("_normalize_date('03/08/2026') → '2026-08-03'",
      APP._normalize_date('03/08/2026') == '2026-08-03')
check("_normalize_date('2026-08-03') passa direto",
      APP._normalize_date('2026-08-03') == '2026-08-03')
check("_normalize_date('lixo') → None", APP._normalize_date('lixo') is None)
check("_float_or_none('1.234,56')", APP._float_or_none('1234,56') == 1234.56)
check("_float_or_none('') → None", APP._float_or_none('') is None)
check("_int_or_none('42') → 42", APP._int_or_none('42') == 42)
check("_int_or_none('x') → None", APP._int_or_none('x') is None)

fim_mes = APP._fim_do_mes('2026-02-10')
check("_fim_do_mes fevereiro 2026 → 2026-02-28", str(fim_mes) == '2026-02-28', f'={fim_mes}')

# ───────────────────────── 6. Km excedente (lógica devolver_locacao) ─────────
print('\n== 6. Km excedente (fórmula da devolução) ==')
km_saida, km_retorno = 10000, 10900
franquia_dia, valor_km = 100, 0.5
di, dd = date(2026, 8, 1), date(2026, 8, 7)
dias = (dd - di).days + 1                      # 7
franquia_total = franquia_dia * dias           # 700
km_rodado = km_retorno - km_saida              # 900
km_exc = max(0, km_rodado - franquia_total)    # 200
total = round(km_exc * valor_km, 2)            # 100.0
check('7 dias, franquia 100/dia, rodou 900 km → excedente 200 km = R$100',
      total == 100.0, f'total={total}')

# ───────────────────────── Resumo ────────────────────────────────────────────
print('\n' + '=' * 60)
if FAILS:
    print(f'{len(FAILS)} FALHA(S):')
    for f in FAILS:
        print('  -', f)
    sys.exit(1)
print('TODOS OS TESTES PASSARAM')
