from uuid import uuid4

import pytest

from cloud_auth import CloudAuth
from cloud_repository import RepositoryError

pytestmark = pytest.mark.integration


def test_bearer_identity_and_current_roles(local_stack, cloud, scope):
    auth = CloudAuth(local_stack["api_url"], local_stack["publishable_key"], scope["nodes"][0])
    admin = cloud["users"][0]
    actor = cloud["users"][1]
    a = auth.authenticate_access(admin["token"])
    b = auth.authenticate_access(actor["token"])
    assert a.user_id != b.user_id
    assert a.profile_id == admin["profile"] and b.profile_id == actor["profile"]
    assert auth.current_roles(a) == ("administrator",)
    cloud["sql"]("delete from public.user_role where profile_id=%s", (a.profile_id,))
    assert auth.current_roles(a) == ()
    assert auth.repository(b).statistics() is not None
    cloud["sql"]("update public.profile set is_active=false where profile_id=%s", (b.profile_id,))
    with pytest.raises(RepositoryError, match="permission"):
        auth.current_roles(b)
    assert not b.active
    with pytest.raises(RepositoryError, match="authentication"):
        auth.authenticate_access(str(uuid4()))
