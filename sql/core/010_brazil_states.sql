-- core.brazil_states — static reference: federative unit -> IBGE macro-region.
CREATE TABLE core.brazil_states (
    state  text PRIMARY KEY,
    region text NOT NULL
);

INSERT INTO core.brazil_states (state, region) VALUES
    ('AC', 'North'), ('AM', 'North'), ('AP', 'North'), ('PA', 'North'),
    ('RO', 'North'), ('RR', 'North'), ('TO', 'North'),
    ('AL', 'Northeast'), ('BA', 'Northeast'), ('CE', 'Northeast'), ('MA', 'Northeast'),
    ('PB', 'Northeast'), ('PE', 'Northeast'), ('PI', 'Northeast'), ('RN', 'Northeast'),
    ('SE', 'Northeast'),
    ('DF', 'Central-West'), ('GO', 'Central-West'), ('MS', 'Central-West'), ('MT', 'Central-West'),
    ('ES', 'Southeast'), ('MG', 'Southeast'), ('RJ', 'Southeast'), ('SP', 'Southeast'),
    ('PR', 'South'), ('RS', 'South'), ('SC', 'South');
