-- Create fleet table
CREATE TABLE fleet (
    van_number TEXT PRIMARY KEY,
    make TEXT,
    drive_train TEXT,
    size_class TEXT,
    tags TEXT[],
    status TEXT DEFAULT 'active'
);

-- Create drivers table
CREATE TABLE drivers (
    driver_name TEXT PRIMARY KEY,
    vehicle_restriction TEXT
);

-- Insert 45 vans
INSERT INTO fleet (van_number, make, drive_train, size_class, tags, status) VALUES 
('1', 'RAM', 'FWD', 'Standard', '{}', 'active'),
('2', 'RAM', 'FWD', 'Standard', '{}', 'active'),
('3', 'RAM', 'FWD', 'Standard', '{}', 'active'),
('4', 'RAM', 'FWD', 'Standard', '{}', 'active'),
('5', 'RAM', 'FWD', 'Standard', '{}', 'active'),
('6', 'RAM', 'FWD', 'Standard', '{}', 'active'),
('7', 'RAM', 'FWD', 'Standard', '{}', 'active'),
('8', 'RAM', 'FWD', 'Standard', '{}', 'active'),
('9', 'RAM', 'FWD', 'Standard', '{}', 'active'),
('10', 'RAM', 'FWD', 'Standard', '{}', 'active'),
('11', 'RAM', 'FWD', 'Standard', '{}', 'active'),
('12', 'RAM', 'FWD', 'Standard', '{}', 'active'),
('13', 'RAM', 'FWD', 'Standard', '{}', 'active'),
('14', 'RAM', 'FWD', 'Standard', '{}', 'active'),
('15', 'RAM', 'FWD', 'Standard', '{}', 'active'),
('16', 'RAM', 'FWD', 'Standard', '{}', 'active'),
('17', 'RAM', 'FWD', 'Standard', '{}', 'active'),
('44', 'RAM', 'FWD', 'Standard', '{}', 'active'),
('18', 'RAM', 'FWD', 'Standard', '{}', 'active'),
('19', 'RAM', 'FWD', 'Standard', '{}', 'active'),
('20', 'RAM', 'FWD', 'Standard', '{}', 'active'),
('21', 'RAM', 'FWD', 'Standard', '{}', 'active'),
('27', 'RAM', 'FWD', 'Standard', '{}', 'active'),
('28', 'RAM', 'FWD', 'Standard', '{}', 'active'),
('22', 'RAM', 'FWD', 'Standard', '{}', 'active'),
('23', 'RAM', 'FWD', 'Standard', '{}', 'active'),
('24', 'RAM', 'FWD', 'Standard', '{}', 'active'),
('25', 'RAM', 'FWD', 'Standard', '{}', 'active'),
('26', 'RAM', 'FWD', 'Standard', '{}', 'active'),
('29', 'Ford', 'AWD', 'Large', '{}', 'active'),
('30', 'Ford', 'AWD', 'Large', '{}', 'active'),
('37', 'Ford', 'AWD', 'Large', '{}', 'active'),
('38', 'Ford', 'AWD', 'Large', '{"island_pass"}', 'active'),
('39', 'Ford', 'AWD', 'Large', '{}', 'active'),
('40', 'Ford', 'AWD', 'Large', '{}', 'active'),
('41', 'Ford', 'AWD', 'Large', '{}', 'active'),
('42', 'Ford', 'AWD', 'Large', '{}', 'active'),
('43', 'Ford', 'AWD', 'Large', '{}', 'active'),
('31', 'Ford', 'RWD', 'Large', '{}', 'active'),
('32', 'Ford', 'RWD', 'Large', '{}', 'active'),
('33', 'Ford', 'RWD', 'Large', '{}', 'active'),
('34', 'Ford', 'RWD', 'Large', '{}', 'active'),
('35', 'Ford', 'RWD', 'Large', '{}', 'active'),
('36', 'Ford', 'RWD', 'Large', '{}', 'active'),
('45', 'Mercedes', 'RWD', 'Large', '{}', 'active');
