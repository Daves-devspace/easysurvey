-- ===============================
-- Fix easydocs_clientdoc schema
-- ===============================

-- Rename wrong column
ALTER TABLE easydocs_clientdoc
    RENAME COLUMN drive_backend TO storage_backend;

-- Add missing fields
ALTER TABLE easydocs_clientdoc
    ADD COLUMN drive_url varchar(200) NULL,
    ADD COLUMN local_path varchar(500) NULL,
    ADD COLUMN failure_reason text NULL;

-- Ensure status column has correct default + choices
ALTER TABLE easydocs_clientdoc
    ALTER COLUMN status SET DEFAULT 'pending';

-- ===============================
-- Fix easydocs_document schema
-- ===============================

-- If "drive_backend" exists, rename it
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name='easydocs_document' AND column_name='drive_backend'
    ) THEN
        ALTER TABLE easydocs_document
            RENAME COLUMN drive_backend TO storage_backend;
    END IF;
END $$;

-- Add missing fields
ALTER TABLE easydocs_document
    ADD COLUMN drive_url varchar(200) NULL,
    ADD COLUMN local_path varchar(500) NULL,
    ADD COLUMN failure_reason text NULL,
    ADD COLUMN drive_file_id varchar(255) NULL,
    ADD COLUMN status varchar(20) DEFAULT 'pending';

-- Add uploaded_by foreign key (if missing)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name='easydocs_document' AND column_name='uploaded_by_id'
    ) THEN
        ALTER TABLE easydocs_document
            ADD COLUMN uploaded_by_id integer NOT NULL,
            ADD CONSTRAINT easydocs_document_uploaded_by_id_fkey
                FOREIGN KEY (uploaded_by_id) REFERENCES auth_user(id) DEFERRABLE INITIALLY DEFERRED;
    END IF;
END $$;

-- ===============================
-- Done
-- ===============================
