import pytest

from sourcelens.query import UnsafeQuery, validate_read_query


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM feedback",
        "SELECT * FROM feedback; DROP TABLE feedback",
        "SELECT * FROM secrets",
        "CREATE TABLE copy AS SELECT * FROM feedback",
    ],
)
def test_rejects_unsafe_sql(sql):
    with pytest.raises(UnsafeQuery):
        validate_read_query(sql)


def test_accepts_allowlisted_read_query():
    assert validate_read_query("SELECT product_id, COUNT(*) FROM feedback GROUP BY product_id")
