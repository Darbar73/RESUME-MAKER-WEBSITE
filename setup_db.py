from app import db, app

print("database connection in process")
with app.app_context():
    db.create_all()
    print("Database connected....")