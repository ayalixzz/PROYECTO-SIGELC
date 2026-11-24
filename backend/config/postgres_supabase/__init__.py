import re


DATABASE_NAME = 'postgres'
DATABASE_ROLE = 'sigelc_demo_app'
SCHEMA = 'sigelc_demo'
POOLER_HOST = re.compile(r'[a-z0-9-]+\.pooler\.supabase\.com')
PROJECT_REF = re.compile(r'[a-z0-9]{20}')


def pooler_project(host, user, *, role):
    prefix, _, project = user.partition('.')
    if not POOLER_HOST.fullmatch(host) or prefix != role or not PROJECT_REF.fullmatch(project):
        raise ValueError('Se requiere el host y usuario del Session pooler de Supabase.')
    return project
