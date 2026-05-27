# Benchmarks SEO

## CTR

O sistema usa benchmark de CTR apenas para paginas:

- Paginas: meta desejada de 0.70% a 1.00%.
- Queries nao recebem benchmark fixo de CTR.
- Query de marca pura ou navegacional, como `lacoste` ou `reserva`: monitoramento e reforco da pagina de marca/categoria.
- Query comercial de marca, como `aramis outlet` ou `reserva loja`: oportunidade de landing/categoria comercial.
- Produto + marca, como `tenis lacoste` ou `camiseta reserva`: oportunidade de guia de compra, hub ou conteudo de apoio.
- Produto generico: oportunidade de FAQ, comparativo ou hub de categoria.
- Query generica: oportunidade editorial apenas quando houver intencao informacional clara.

## Posicao

- Posicao media geral desejada: abaixo de 6.00.
- Posicao media para marcas/produtos: abaixo de 6.00.

## Limpeza de tarefas antigas

Para remover tarefas antigas de CTR criadas antes dessa calibracao, rode no Supabase SQL Editor:

```sql
-- arquivo: supabase/cleanup_legacy_ctr_tasks.sql
```

Depois, regenere o backlog:

```bash
python run.py --module backlog --gsc ./gsc_exports --save-db --no-report
```

## Memoria de mudancas

O backlog le automaticamente um CSV de controle SEO para evitar tarefas que ja foram implementadas.

Colunas esperadas:

- `Data`
- `URL`
- `Tipo de Mudanca`
- `Elemento Alterado`
- `Antes`
- `Depois`
- `Status`
- `Observacoes`

Status que contam como realizado: `Implementado`, `Publicado`, `Concluido`, `Feito`, `Done`, `Completed`, `Aplicado`.

Uso manual:

```bash
python run.py --module change-memory --changes-log "C:\Users\User\Downloads\controle_seo_dashboard.xlsx - Controle SEO.csv"
```

Uso no backlog:

```bash
python run.py --module backlog --gsc ./gsc_exports --changes-log "C:\Users\User\Downloads\controle_seo_dashboard.xlsx - Controle SEO.csv"
```

Tambem e possivel fixar no `.env`:

```env
SEO_CHANGELOG_CSV=C:\Users\User\Downloads\controle_seo_dashboard.xlsx - Controle SEO.csv
```
