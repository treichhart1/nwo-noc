"""Private licensed cellular observation layers, never authoritative NOC towers."""
from alembic import op
revision = '0036_mikrotik_cellular'
down_revision = '0035_unified_communications'
branch_labels = None
depends_on = None

def upgrade():
    op.execute('\nCREATE TABLE cellular_datasets (\n\tid SERIAL NOT NULL, \n\tdigest VARCHAR(64) NOT NULL, \n\tsource VARCHAR(30) NOT NULL, \n\tversion VARCHAR(120) NOT NULL, \n\tattribution VARCHAR(500) NOT NULL, \n\tlicense VARCHAR(120) NOT NULL, \n\tapproval_reference VARCHAR(120) NOT NULL, \n\tobserved_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\timported_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tactor_id INTEGER NOT NULL, \n\trow_count INTEGER NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (digest), \n\tFOREIGN KEY(actor_id) REFERENCES users (id)\n)\n\n')
    op.execute("\nCREATE TABLE cellular_site_observations (\n\tid SERIAL NOT NULL, \n\tdataset_id INTEGER NOT NULL, \n\tidentity VARCHAR(180) NOT NULL, \n\tkind VARCHAR(30) NOT NULL, \n\ttechnology VARCHAR(20), \n\tmcc INTEGER, \n\tmnc INTEGER, \n\tarea VARCHAR(30), \n\tcell VARCHAR(30), \n\tlatitude FLOAT NOT NULL, \n\tlongitude FLOAT NOT NULL, \n\tuncertainty_m FLOAT, \n\tobserved_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tconfidence VARCHAR(20) NOT NULL, \n\tband VARCHAR(20), \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_cellular_dataset_identity UNIQUE (dataset_id, identity), \n\tCONSTRAINT ck_cellular_coordinates CHECK (latitude >= -90 AND latitude <= 90 AND longitude >= -180 AND longitude <= 180), \n\tCONSTRAINT ck_cellular_kind CHECK (kind IN ('estimated_cell','registered_structure','mapped_structure')), \n\tFOREIGN KEY(dataset_id) REFERENCES cellular_datasets (id)\n)\n\n")
    op.execute('CREATE INDEX ix_cellular_bounds ON cellular_site_observations (latitude, longitude)')
    op.execute('CREATE INDEX ix_cellular_cell_identity ON cellular_site_observations (mcc, mnc, technology, area, cell)')
    op.execute('CREATE INDEX ix_cellular_site_observations_dataset_id ON cellular_site_observations (dataset_id)')
    op.execute('CREATE INDEX ix_cellular_site_observations_observed_at ON cellular_site_observations (observed_at)')

def downgrade():
    op.drop_table('cellular_site_observations')
    op.drop_table('cellular_datasets')
