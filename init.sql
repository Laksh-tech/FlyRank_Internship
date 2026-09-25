CREATE TABLE IF NOT EXISTS tasks (
    id SERIAL PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    status BOOLEAN NOT NULL DEFAULT FALSE
);

-- Seed initial records if empty
INSERT INTO tasks (title, status) 
SELECT 'Buy MILK', false WHERE NOT EXISTS (SELECT 1 FROM tasks);
INSERT INTO tasks (title, status) 
SELECT 'Read Book', false WHERE NOT EXISTS (SELECT 1 FROM tasks);
INSERT INTO tasks (title, status) 
SELECT 'WorkOut', false WHERE NOT EXISTS (SELECT 1 FROM tasks);