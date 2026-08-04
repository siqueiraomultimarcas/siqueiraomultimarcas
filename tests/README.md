# Testes — Siqueirão Multimarcas

Três suítes independentes que rodam **sem banco e sem tocar no Asaas**
(nenhuma cobrança é gerada). Analisam código-fonte e lógica pura.

## Como rodar

```bash
cd siqueiraomultimarcas
python tests/test_calculos.py    # cálculo de contratos, períodos e km excedente
python tests/test_seguranca.py   # auth, SQL injection, segredos, brute force, XSS
python tests/test_templates.py   # Jinja, XSS em onclick, paleta, ids órfãos
```

Saída `TODOS OS TESTES PASSARAM` / `NENHUMA VULNERABILIDADE ENCONTRADA` /
`TEMPLATES OK` indica sucesso. Exit code 1 em qualquer falha.

## O que cada suíte cobre

**test_calculos.py** — usa um cursor falso para exercitar
`_gerar_periodos_futuros` sem banco:
- períodos semanal (7d) / quinzenal (14d) / mensal (28d) fecham certo
- período cheio usa `valor_semanal`; período parcial cobra `diaria × dias`
- períodos contíguos, sem furo nem sobreposição, terminando em 31/dez
- idempotência: rodar duas vezes não duplica registros
- convenção de dias **inclusiva** (03→09 = 7 dias) igual em todos os endpoints
- helpers `_normalize_date`, `_float_or_none`, `_int_or_none`, `_fim_do_mes`
- fórmula de km excedente da devolução

**test_seguranca.py** — auditoria estática do `app.py`:
- toda rota privada tem `@login_required`
- nenhuma interpolação de input em SQL (só placeholders `%s`)
- todo DELETE financeiro exige `@admin_required`
- flags de cookie, `SECRET_KEY` obrigatória, `MAX_CONTENT_LENGTH`
- senhas com hash, nenhum segredo hardcoded
- headers de segurança, proteção contra open redirect
- brute force persistido em banco e aplicado antes de validar a senha
- upload valida extensão **e** magic bytes

**test_templates.py** — integridade das telas:
- blocos Jinja balanceados e herdados de um parent que os define
- nenhum texto do banco interpolado cru em atributo `onclick`
- nenhum `alert()` / `confirm()` / `prompt()` nativo
- paleta unificada nas telas repaginadas
- todo `getElementById` aponta para um id que existe
