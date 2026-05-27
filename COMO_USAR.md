# SEO Audit — Secret Outlet

## 1. Instalar Python

Baixe o Python 3.11+ em: https://www.python.org/downloads/
Na instalação, marque a opção **"Add Python to PATH"**.

## 2. Instalar dependências

Abra o terminal na pasta `seo-audit` e rode:

```
pip install -r requirements.txt
```

## 3. Usar os módulos

### Analisar dados do Google Search Console
Copie os CSVs exportados do GSC para a pasta `gsc_exports/` e rode:
```
python run.py --module gsc --gsc ./gsc_exports
```

### Auditar on-page das páginas prioritárias
```
python run.py --module onpage
```

Ou páginas específicas:
```
python run.py --module onpage --urls /lacoste /tommy-hilfiger /columbia
```

### Verificar conteúdo duplicado
```
python run.py --module duplicates
```

### Checar links quebrados e páginas órfãs
```
python run.py --module broken-links --max-pages 200
```

### Analisar clusters e links internos
```
python run.py --module clusters
```

### Auditoria de velocidade (PageSpeed / Core Web Vitals)
```
python run.py --module pagespeed --urls /lacoste /tommy-hilfiger /columbia
```

### Rodar TUDO de uma vez
```
python run.py --all --gsc ./gsc_exports
```

## Limpar banco Supabase

Para ver quantos registros existem antes/depois:

```sql
-- Rode no Supabase SQL Editor
-- arquivo: supabase/check_counts.sql
```

Para apagar todos os dados e manter a estrutura das tabelas:

```sql
-- Rode no Supabase SQL Editor
-- arquivo: supabase/clear_database.sql
```

Depois, gere novos dados:

```bash
python run.py --module backlog --gsc ./gsc_exports --top 20 --save-db --no-report
```

## 4. Ver o relatório

O relatório HTML é gerado automaticamente na pasta `reports/` e abre no navegador.
Arquivo: `reports/seo_audit_YYYY-MM-DD_HH-MM.html`

## 5. Configurar o site

Edite `config.py` para ajustar:
- `PRIORITY_PAGES` — lista de páginas a auditar
- `BRAND_CLUSTERS` — estrutura dos clusters por marca
- `PAGESPEED_API_KEY` — chave da API do Google (opcional, gratuita)
- `MAX_CRAWL_PAGES` — limite de páginas no rastreamento
