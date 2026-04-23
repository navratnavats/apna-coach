from supabase import Client, create_client

from app.config import SUPABASE_SERVER_KEY, SUPABASE_URL

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVER_KEY)

