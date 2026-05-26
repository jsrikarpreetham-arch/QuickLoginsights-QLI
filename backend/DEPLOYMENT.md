# QuickLogInsights Backend - Production Configuration for High-Volume Logs

## Setup for 1000+ Logs

### 1. Database Optimization

```sql
-- Analyze query performance
ANALYZE users;
ANALYZE logs;
ANALYZE incidents;

-- Rebuild indexes for better performance
REINDEX TABLE logs;
REINDEX TABLE incidents;

-- Create additional indexes for common queries
CREATE INDEX idx_logs_user_timestamp ON logs(user_id, timestamp DESC);
CREATE INDEX idx_logs_severity_timestamp ON logs(severity, timestamp DESC);
CREATE INDEX idx_incidents_user_status ON incidents(user_id, status);
```

### 2. PostgreSQL Connection Pool Optimization

In `.env`:
```
DATABASE_URL=postgresql://user:pass@host:5432/db?pool=20&max_overflow=40
```

### 3. Batch Ingestion Endpoint

Instead of sending individual logs, use batch ingestion:

```bash
curl -X POST http://localhost:5000/api/logs/batch-ingest \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "logs": [
      {
        "source": "app-server",
        "severity": "ERROR",
        "message": "Connection timeout",
        "metadata": {"endpoint": "/api/users"},
        "timestamp": "2026-05-26T10:30:00Z"
      },
      ...  // up to 1000+ logs
    ]
  }'
```

### 4. API Endpoints for High-Volume Data

**Get log statistics:**
```bash
GET /api/logs/stats
```

**Get logs by source:**
```bash
GET /api/logs/source/app-server?page=1&per_page=50
```

**Get logs by severity:**
```bash
GET /api/logs/severity/ERROR?page=1&per_page=50
```

**Batch process for anomalies:**
```bash
POST /api/logs/batch-anomalies
```

**Export logs to CSV:**
```bash
GET /api/logs/export?format=csv&days=7
```

### 5. Performance Tips

1. **Batch Ingestion**: Send logs in batches of 500-1000 instead of one-by-one
2. **Pagination**: Use pagination with `per_page=50` to `per_page=100` for best performance
3. **Filtering**: Apply filters (severity, source) to reduce data volume
4. **Timestamps**: Always include proper timestamps for better indexing
5. **Archival**: Delete logs older than 30 days using the cleanup endpoint

### 6. Deployment with Gunicorn (Production)

```bash
# Multiple workers for concurrent requests
gunicorn -w 4 -b 0.0.0.0:5000 --timeout 120 app:app

# For high-traffic scenarios
gunicorn -w 8 --worker-class sync -b 0.0.0.0:5000 --timeout 120 --max-requests 1000 app:app
```

### 7. Database Connection Pooling

SQLAlchemy with connection pooling for concurrent requests:

```python
SQLALCHEMY_ENGINE_OPTIONS = {
    "pool_size": 20,
    "pool_recycle": 3600,
    "pool_pre_ping": True,
    "max_overflow": 40,
}
```

### 8. Monitoring & Logging

```python
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('app.log'),
        logging.StreamHandler()
    ]
)
```

### 9. Cache Configuration

The backend uses in-memory caching for:
- User log statistics
- Anomaly results
- Incident summaries

Cache timeout: 5 minutes

### 10. Scalability Considerations

For even larger volumes (10,000+ logs/day):

1. **Use Redis for caching**: `CACHE_TYPE: redis`
2. **Implement message queue**: Use Celery for async processing
3. **Database partitioning**: Partition logs by date ranges
4. **Read replicas**: Use PostgreSQL read replicas for reporting
5. **Time-series DB**: Consider InfluxDB for metrics instead of logs

### 11. Load Testing

Test with Apache Bench:

```bash
# Batch ingest 100 requests with 1000 logs each
ab -n 100 -c 10 -p logs.json -T application/json http://localhost:5000/api/logs/batch-ingest
```

### 12. Example Batch Ingest Payload (1000 logs)

```json
{
  "logs": [
    {
      "source": "api-server",
      "severity": "INFO",
      "message": "Request processed",
      "metadata": {"duration_ms": 125},
      "timestamp": "2026-05-26T12:00:00Z"
    },
    // ... repeat 999 more times
  ]
}
```

### Monitoring Queries

```sql
-- Check log ingestion rate
SELECT COUNT(*) as total_logs, 
       DATE_TRUNC('hour', timestamp) as hour
FROM logs 
GROUP BY hour 
ORDER BY hour DESC;

-- Find slow queries
SELECT query, calls, mean_time FROM pg_stat_statements 
ORDER BY mean_time DESC LIMIT 10;

-- Check index usage
SELECT schemaname, tablename, indexname, idx_scan 
FROM pg_stat_user_indexes 
ORDER BY idx_scan DESC;
```

## Summary

The backend is optimized for:
- ✅ 1000+ concurrent log ingestion
- ✅ Fast retrieval and filtering
- ✅ Anomaly detection on large datasets
- ✅ AI-powered analysis
- ✅ Horizontal scalability
