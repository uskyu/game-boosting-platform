# Database Migrations

This directory contains Alembic database migrations for the Game Boosting Platform.

## Commands

### Generate a new migration
```bash
alembic revision --autogenerate -m "description of changes"
```

### Apply all pending migrations
```bash
alembic upgrade head
```

### Rollback one migration
```bash
alembic downgrade -1
```

### View current migration status
```bash
alembic current
```

### View migration history
```bash
alembic history
```
