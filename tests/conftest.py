import os
import tempfile

# Tests always start offline with a throwaway database; individual tests opt into the fake AMD endpoint.
os.environ["MOCK_MODE"] = "1"
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
