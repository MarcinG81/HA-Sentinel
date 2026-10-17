-- v5: notification outbox with channel, message id and dismissal (spec 14.3)

CREATE TABLE notification_outbox_v5 (
  id TEXT PRIMARY KEY,
  incident_id TEXT NOT NULL REFERENCES incidents(id),
  revision INTEGER NOT NULL,
  channel TEXT NOT NULL DEFAULT 'persistent_notification',
  reason TEXT NOT NULL DEFAULT 'opened',
  state TEXT NOT NULL CHECK (state IN
    ('pending', 'sent', 'uncertain', 'failed', 'skipped', 'dismissed')),
  attempt INTEGER NOT NULL DEFAULT 0,
  next_try_us INTEGER NOT NULL,
  created_us INTEGER NOT NULL DEFAULT 0,
  sent_us INTEGER,
  UNIQUE(incident_id, revision, channel)
);
INSERT INTO notification_outbox_v5(id, incident_id, revision, state, attempt, next_try_us,
                                   created_us)
  SELECT id, incident_id, revision, state, attempt, next_try_us, next_try_us
  FROM notification_outbox;
DROP TABLE notification_outbox;
ALTER TABLE notification_outbox_v5 RENAME TO notification_outbox;
CREATE INDEX notification_outbox_due ON notification_outbox(state, next_try_us);
