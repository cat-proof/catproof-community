"""Apply Alembic migrations before accepting HTTP requests."""
import os
from pathlib import Path
from dotenv import load_dotenv

# Deployment environment always wins; local secrets stay in ignored files.
ROOT=Path(__file__).resolve().parent
load_dotenv(ROOT/'.env.local',override=False)
load_dotenv(ROOT/'.env',override=False)

# The standalone registry keeps session-scoped SET ROLE and tenant settings.
# Transaction-mode poolers cannot preserve those settings across statements.
if os.environ.get('DATABASE_URL_UNPOOLED'):
    os.environ['DATABASE_URL']=os.environ['DATABASE_URL_UNPOOLED']

from database import upgrade_hosted
from hosted_app import main

if __name__=='__main__':
    upgrade_hosted(os.environ.get('DATABASE_URL_UNPOOLED') or None)
    main()
