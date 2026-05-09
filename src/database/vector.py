import chromadb
import os

def init_chroma():
    """
    Initializes and returns a ChromaDB collection for the RAG chat history.
    """
    # Assuming ChromaDB container is accessible at the default host/port defined in docker-compose.yml
    # Or fallback to an ephemeral client if we are running locally without docker

    # We will just setup the client here
    # In production with docker-compose, the host would be "chromadb"
    # But for a local dev setup it could be "localhost"

    chroma_host = os.getenv("CHROMA_HOST", "localhost")
    chroma_port = os.getenv("CHROMA_PORT", "8000")

    try:
        client = chromadb.HttpClient(host=chroma_host, port=chroma_port)
        # Try to get or create collection
        collection = client.get_or_create_collection(name="thronebound_history")
        return collection
    except Exception as e:
        print(f"Failed to connect to ChromaDB via HttpClient: {e}. Falling back to PersistentClient in data/chroma_data")
        # Fallback to local persistent client for testing outside docker
        client = chromadb.PersistentClient(path="./data/chroma_data")
        collection = client.get_or_create_collection(name="thronebound_history")
        return collection

def insert_history(collection, kingdom_id: int, text: str):
    import uuid
    doc_id = str(uuid.uuid4())
    collection.add(
        documents=[text],
        metadatas=[{"kingdom_id": kingdom_id}],
        ids=[doc_id]
    )

def query_history(collection, kingdom_id: int, query_text: str, n_results: int = 3):
    results = collection.query(
        query_texts=[query_text],
        n_results=n_results,
        where={"kingdom_id": kingdom_id}
    )
    # results['documents'] is a list of lists of strings
    if results and 'documents' in results and len(results['documents']) > 0:
        return results['documents'][0]
    return []
