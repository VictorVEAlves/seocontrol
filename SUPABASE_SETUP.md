# Supabase Setup

## 1. Criar as tabelas

Abra o SQL Editor do Supabase e rode o arquivo:

```sql
supabase/schema.sql
```

Esse script cria:

- `sites`
- `urls`
- `crawl_runs`
- `page_snapshots`
- `gsc_queries`
- `gsc_pages`
- `issues`
- `recommendations`
- `content_changes`

Se aparecer erro de RLS como `new row violates row-level security policy`, rode tambem:

```sql
supabase/fix_rls.sql
```

## 2. Configurar variaveis

No `.env`, mantenha:

```env
SUPABASE_URL=https://seu-projeto.supabase.co
SUPABASE_KEY=sua_chave_publicavel_aqui
```

Opcional e recomendado para uso server-side:

```env
SUPABASE_SERVICE_ROLE_KEY=sua_chave_service_role_aqui
```

## 3. Rodar auditoria salvando no banco

```bash
python run.py --module backlog --gsc ./gsc_exports --urls /lacoste --top 20 --save-db --no-report
```

Ou uma auditoria completa:

```bash
python run.py --all --gsc ./gsc_exports --max-pages 200 --save-db
```

Para exportar o backlog em CSV e HTML:

```bash
python run.py --module backlog --gsc ./gsc_exports --top 20 --export-backlog --no-report
```

Novos modulos operacionais:

```bash
python run.py --module sitemap --urls /lacoste --no-report
python run.py --module indexability --urls /lacoste --no-report
python run.py --module snippets --urls /lacoste --no-report
python run.py --module content-gap --gsc ./gsc_exports --urls /lacoste --no-report
python run.py --module products --urls /lacoste --no-report
python run.py --module link-suggestions --urls /lacoste --no-report
python run.py --module regression --no-report
```

Diagnostico do ambiente:

```bash
python run.py --module doctor --no-report
```

Monitor recorrente:

```bash
powershell -ExecutionPolicy Bypass -File scripts/run_monitor.ps1
```

Fila de conteudo:

```bash
python run.py --module generate --urls /lacoste --save-db
python run.py --module content-sync --no-report
```

Ideias de blog geradas por queries do GSC:

```bash
python run.py --module blog-ideas --gsc ./gsc_exports --top 20 --save-db --no-report
```

Exemplo de transformacao:

```text
query: moletom lacoste
ideia: Melhores moletons Lacoste
```

Dashboard:

```bash
python app.py
```

Rotas principais:

- `/` painel executivo
- `/issues` issues abertas com filtros e acao de resolver
- `/recommendations` backlog com acao de concluir
- `/content` fila de conteudo gerado
- `/blog-ideas` ideias de blog por query
- `/runs` historico de execucoes

## 4. Ver painel local

```bash
python app.py
```

Abra:

```text
http://127.0.0.1:5000
```
