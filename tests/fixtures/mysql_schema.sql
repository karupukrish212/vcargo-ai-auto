-- SYNTHETIC FIXTURE ONLY. A DBA may apply this to a disposable test database.
-- Never run this file against the ERP. The application/test runner never executes it.
CREATE TABLE fixture_parent (
  record_id INT UNSIGNED NOT NULL COMMENT 'Synthetic identifier',
  revision INT UNSIGNED NOT NULL,
  label VARCHAR(80) NULL DEFAULT '' COMMENT 'Synthetic label',
  PRIMARY KEY (record_id, revision),
  UNIQUE KEY uq_fixture_label (label)
) ENGINE=InnoDB COMMENT='Synthetic parent for integration tests';

CREATE TABLE fixture_child (
  record_id INT UNSIGNED NOT NULL,
  revision INT UNSIGNED NOT NULL,
  note VARCHAR(80) NULL,
  PRIMARY KEY (record_id, revision),
  CONSTRAINT fk_fixture_parent FOREIGN KEY (record_id, revision)
    REFERENCES fixture_parent (record_id, revision)
    ON UPDATE RESTRICT ON DELETE RESTRICT,
  CONSTRAINT chk_fixture_revision CHECK (revision > 0)
) ENGINE=InnoDB COMMENT='Synthetic child for integration tests';
