BEGIN;

ALTER TABLE nodes
  ADD COLUMN IF NOT EXISTS node_index INT;

-- Backfill determinístico para versões já existentes (ordena por path + node_id)
WITH ranked AS (
  SELECT node_id,
         row_number() OVER (PARTITION BY version_id ORDER BY path, node_id) - 1 AS rn
  FROM nodes
)
UPDATE nodes n
SET node_index = r.rn
FROM ranked r
WHERE n.node_id = r.node_id AND n.node_index IS NULL;

-- Para o MVP: exigir node_index sempre preenchido
ALTER TABLE nodes
  ALTER COLUMN node_index SET NOT NULL;

CREATE INDEX IF NOT EXISTS ix_nodes_version_node_index
  ON nodes(version_id, node_index);

COMMIT;
