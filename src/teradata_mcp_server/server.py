import os
import asyncio
import logging
import signal
import json
import yaml
from typing import Any, List, Optional
from pydantic import Field, BaseModel
import mcp.types as types
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.prompts.base import UserMessage, TextContent
from dotenv import load_dotenv
import tdfs4ds
import teradataml as tdml


# Import the ai_tools module, clone-and-run friendly
try:
    from teradata_mcp_server import tools as td
except ImportError:
    import tools as td

load_dotenv()

os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(),
              logging.FileHandler(os.path.join("logs", "teradata_mcp_server.log"))],
)
logger = logging.getLogger("teradata_mcp_server")    
logger.info("Starting Teradata MCP server")

# Connect to MCP server
mcp = FastMCP("teradata-mcp-server")

#global shutdown flag
shutdown_in_progress = False

# Initiate connection to Teradata
_tdconn = td.TDConn()

#afm-defect:
_enableEVS = False

# Only attempt to connect to EVS is the system has an EVS installed/configured
if len(os.getenv("VS_NAME", "").strip()) > 0:
    try:
        _evs = td.evs.get_evs()
        _enableEVS = True
        logger.info("Connection to Enterprise Vector Store is successful.")
    except Exception as e:
        logger.error(f"Unable to establish connection to EVS: {e}")

        
#afm-defect: moved establish teradataml connection into main TDConn to enable auto-reconnect.
#td.teradataml_connection()




#------------------ Tool utilies  ------------------#
ResponseType = List[types.TextContent | types.ImageContent | types.EmbeddedResource]

def format_text_response(text: Any) -> ResponseType:
    """Format a text response."""
    if isinstance(text, str):
        try:
            # Try to parse as JSON if it's a string
            parsed = json.loads(text)
            return [types.TextContent(
                type="text", 
                text=json.dumps(parsed, indent=2, ensure_ascii=False)
            )]
        except json.JSONDecodeError:
            # If not JSON, return as plain text
            return [types.TextContent(type="text", text=str(text))]
    # For non-string types, convert to string
    return [types.TextContent(type="text", text=str(text))]

def format_error_response(error: str) -> ResponseType:
    """Format an error response."""
    return format_text_response(f"Error: {error}")

def execute_db_tool(tool, *args, **kwargs):
    """Execute a database tool with the given connection and arguments."""
    global _tdconn
    try:
        if not _tdconn.conn:
            logger.info("Reinitializing TDConn")
            _tdconn = td.TDConn()  # Reinitialize connection if not connected
        return format_text_response(tool(_tdconn, *args, **kwargs))
    except Exception as e:
        logger.error(f"Error sampling object: {e}")
        return format_error_response(str(e))
    

def execute_vs_tool(tool, *args, **kwargs) -> ResponseType:
    global _evs
    global _enableEVS

    if _enableEVS:
        try:
            return format_text_response(tool(_evs, *args, **kwargs))
        except Exception as e:
            if "401" in str(e) or "Session expired" in str(e):
                logger.warning("EVS session expired, refreshing …")
                _evs = td.evs_connect.refresh_evs()
                try:
                    return format_text_response(tool(_evs, *args, **kwargs))
                except Exception as retry_err:
                    logger.error(f"EVS retry failed: {retry_err}")
                    return format_error_response(f"After refresh, still failed: {retry_err}")
    
            logger.error(f"EVS tool error: {e}")
            return format_error_response(str(e))
    else:
        return format_error_response("Enterprise Vector Store is not available on this server.")

    
#------------------ Base Tools  ------------------#

@mcp.tool(description="Executes a SQL query to read from the database.")
async def base_readQuery(
    sql: str = Field(description="SQL that reads from the database to run", default=""),
    ) -> ResponseType:
    """Executes a SQL query to read from the database."""
    return execute_db_tool( td.handle_base_readQuery, sql=sql) 


@mcp.tool(description="Executes a SQL query to write to the database.")
async def base_writeQuery(
    sql: str = Field(description="SQL that writes to the database to run", default=""),
    ) -> ResponseType:
    """Executes a SQL query to write to the database."""
    return execute_db_tool( td.handle_base_writeQuery, sql=sql) 


@mcp.tool(description="Display table DDL definition.")
async def base_tableDDL(
    db_name: str = Field(description="Database name", default=""),
    table_name: str = Field(description="table name", default=""),
    ) -> ResponseType:
    """Display table DDL definition."""
    return execute_db_tool( td.handle_base_tableDDL, db_name=db_name, table_name=table_name)    

@mcp.tool(description="List all databases in the Teradata System.")
async def base_databaseList() -> ResponseType:
    """List all databases in the Teradata System."""
    return execute_db_tool( td.handle_base_databaseList)


@mcp.tool(description="List objects in a database.")
async def base_tableList(
    db_name: str = Field(description="database name", default=""),
    ) -> ResponseType:
    """List objects in a database."""
    return execute_db_tool( td.handle_base_tableList, db_name=db_name)


@mcp.tool(description="Show detailed column information about a database table.")
async def base_columnDescription(
    db_name: str = Field(description="Database name", default=""),
    obj_name: str = Field(description="table name", default=""),
    ) -> ResponseType:
    """Show detailed column information about a database table."""
    return execute_db_tool( td.handle_base_columnDescription, db_name=db_name, obj_name=obj_name)


@mcp.tool(description="Get data samples and structure overview from a database table.")
async def base_tablePreview(
    db_name: str = Field(description="Database name", default=""),
    table_name: str = Field(description="table name", default=""),
    ) -> ResponseType:
    """Get data samples and structure overview from a database table."""
    return execute_db_tool( td.handle_base_tablePreview, table_name=table_name, db_name=db_name)

@mcp.tool(description="Get tables commonly used together by database users, this is helpful to infer relationships between tables.")
async def base_tableAffinity(
    db_name: str = Field(description="Database name", default=""),
    obj_name: str = Field(description="Table or view name", default=""),
    ) -> ResponseType:
    """Get tables commonly used together by database users, this is helpful to infer relationships between tables."""
    return execute_db_tool( td.handle_base_tableAffinity, obj_name=obj_name, db_name=db_name)


@mcp.tool(description="Measure the usage of a table and views by users in a given schema, this is helpful to infer what database objects are most actively used or drive most value.")
async def base_tableUsage(
    db_name: str = Field(description="Database name", default=""),
    ) -> ResponseType:
    """Measure the usage of a table and views by users in a given schema, this is helpful to infer what database objects are most actively used or drive most value."""
    return execute_db_tool( td.handle_base_tableUsage, db_name=db_name)

@mcp.prompt()
async def base_query(qry: str) -> UserMessage:
    """Create a SQL query against the database"""
    return UserMessage(role="user", content=TextContent(type="text", text=td.handle_base_query.format(qry=qry)))

@mcp.prompt()
async def base_tableBusinessDesc(database_name: str, table_name: str) -> UserMessage:
    """Create a business description of the table and columns."""
    return UserMessage(role="user", content=TextContent(type="text", text=td.handle_base_tableBusinessDesc.format(database_name=database_name, table_name=table_name)))

@mcp.prompt()
async def base_databaseBusinessDesc(database_name: str) -> UserMessage:
    """Create a business description of the database."""
    return UserMessage(role="user", content=TextContent(type="text", text=td.handle_base_databaseBusinessDesc.format(database_name=database_name)))

#------------------ DBA Tools  ------------------#

@mcp.tool(description="Get a list of SQL run by a user in the last number of days if a user name is provided, otherwise get list of all SQL in the last number of days.")
async def dba_userSqlList(
    user_name: str = Field(description="user name", default=""),
    no_days: int = Field(description="number of days to look back", default=7),
    ) -> ResponseType:
    """Get a list of SQL run by a user."""
    return execute_db_tool( td.handle_dba_userSqlList, user_name=user_name, no_days=no_days)

@mcp.tool(description="Get a list of SQL run against a table in the last number of days ")
async def dba_tableSqlList(
    table_name: str = Field(description="table name", default=""),
    no_days: int = Field(description="number of days to look back", default=7),
    ) -> ResponseType:
    """Get a list of SQL run by a user."""
    return execute_db_tool( td.handle_dba_tableSqlList, table_name=table_name, no_days=no_days)

@mcp.tool(description="Get table space used for a table if table name is provided or get table space for all tables in a database if a database name is provided.")
async def dba_tableSpace(
    db_name: str = Field(description="Database name", default=""),
    table_name: str = Field(description="table name", default=""),
    ) -> ResponseType:
    """Get table space used for a table if table name is provided or get table space for all tables in a database if a database name is provided."""
    return execute_db_tool( td.handle_dba_tableSpace, db_name=db_name, table_name=table_name)

@mcp.tool(description="Get database space if database name is provided, otherwise get all databases space allocations.")
async def dba_databaseSpace(
    db_name: str = Field(description="Database name", default=""),
    ) -> ResponseType:
    """Get database space if database name is provided, otherwise get all databases space allocations."""
    return execute_db_tool( td.handle_dba_databaseSpace, db_name=db_name)

@mcp.tool(description="Get Teradata database version information.")
async def dba_databaseVersion() -> ResponseType:
    """Get Teradata database version information."""
    return execute_db_tool( td.handle_dba_databaseVersion)

@mcp.tool(description="Get the Teradata system usage summary metrics by weekday and hour for each workload type and query complexity bucket.")
async def dba_resusageSummary() -> ResponseType:
    """Get the Teradata system usage summary metrics by weekday and hour."""
    return execute_db_tool( td.handle_dba_resusageSummary, dimensions=["hourOfDay", "dayOfWeek"])

@mcp.tool(description="Get the Teradata system usage summary metrics by user on a specified date, or day of week and hour of day.")
async def dba_resusageUserSummary(
    user_name: str = Field(description="Database user name", default=""),
    date: str = Field(description="Date to analyze, formatted as `YYYY-MM-DD`", default=""),
    dayOfWeek: str = Field(description="Day of week to analyze", default=""),
    hourOfDay: str = Field(description="Hour of day to analyze", default=""),
    ) -> ResponseType:
    """Get the Teradata system usage summary metrics by user on a specified date, or day of week and hour of day."""
    return execute_db_tool( td.handle_dba_resusageSummary, dimensions=["UserName", "hourOfDay", "dayOfWeek"], user_name=user_name, date=date, dayOfWeek=dayOfWeek, hourOfDay=hourOfDay)

@mcp.tool(description="Get the Teradata flow control metrics.")
async def dba_flowControl() -> ResponseType:
    """Get the Teradata flow control metrics."""
    return execute_db_tool( td.handle_dba_flowControl)

@mcp.tool(description="Get the user feature usage metrics.")
async def dba_featureUsage() -> ResponseType:
    """Get the user feature usage metrics.""" 
    return execute_db_tool( td.handle_dba_featureUsage)

@mcp.tool(description="Get the Teradata user delay metrics.")
async def dba_userDelay() -> ResponseType:
    """Get the Teradata user delay metrics."""
    return execute_db_tool( td.handle_dba_userDelay)

@mcp.tool(description="Measure the usage of a table and views by users, this is helpful to understand what user and tables are driving most resource usage at any point in time.")
async def dba_tableUsageImpact(
    db_name: str = Field(description="Database name", default=""),
    user_name: str = Field(description="User name", default=""),    
    ) -> ResponseType:
    """Measure the usage of a table and views by users, this is helpful to understand what user and tables are driving most resource usage at any point in time."""
    return execute_db_tool( td.handle_dba_tableUsageImpact, db_name=db_name,  user_name=user_name)

@mcp.tool(description="Get the Teradata session information for user.")
async def dba_sessionInfo(
    user_name: str = Field(description="User name", default=""),
    ) -> ResponseType:
    """Get the Teradata session information for user."""
    return execute_db_tool( td.handle_dba_sessionInfo, user_name=user_name)


@mcp.prompt()
async def dba_databaseHealthAssessment() -> UserMessage:
    """Create a database health assessment for a Teradata system."""
    return UserMessage(role="user", content=TextContent(type="text", text=td.handle_dba_databaseHealthAssessment))

@mcp.prompt()
async def dba_userActivityAnalysis() -> UserMessage:
    """Create a user activity analysis for a Teradata system."""
    return UserMessage(role="user", content=TextContent(type="text", text=td.handle_dba_userActivityAnalysis))

@mcp.prompt()
async def dba_tableArchive() -> UserMessage:
    """Create a table archive strategy for database tables."""
    return UserMessage(role="user", content=TextContent(type="text", text=td.handle_dba_tableArchive))

@mcp.prompt()
async def dba_databaseLineage(database_name: str, number_days: int) -> UserMessage:
    """Create a database lineage map for tables in a database."""
    return UserMessage(role="user", content=TextContent(type="text", text=td.handle_dba_databaseLineage.format(database_name=database_name, number_days=number_days)))

@mcp.prompt()
async def dba_tableDropImpact(database_name: str, table_name: str, number_days: int) -> UserMessage:
    """Assess the impact of dropping a table."""
    return UserMessage(role="user", content=TextContent(type="text", text=td.handle_dba_tableDropImpact.format(database_name=database_name, table_name=table_name, number_days=number_days)))

#------------------ Data Quality Tools  ------------------#

@mcp.tool(description="Get the column names that having missing values in a table.")
async def qlty_missingValues(
    table_name: str = Field(description="table name", default=""),
    ) -> ResponseType:
    """Get the column names that having missing values in a table."""
    return execute_db_tool( td.handle_qlty_missingValues, table_name=table_name)

@mcp.tool(description="Get the column names that having negative values in a table.")
async def qlty_negativeValues(
    table_name: str = Field(description="table name", default=""),
    ) -> ResponseType:
    """Get the column names that having negative values in a table."""
    return execute_db_tool( td.handle_qlty_negativeValues, table_name=table_name)

@mcp.tool(description="Get the destinct categories from column in a table.")
async def qlty_distinctCategories(
    table_name: str = Field(description="table name", default=""),
    col_name: str = Field(description="column name", default=""),
    ) -> ResponseType:
    """Get the destinct categories from column in a table."""
    return execute_db_tool( td.handle_qlty_distinctCategories, table_name=table_name, col_name=col_name)

@mcp.tool(description="Get the standard deviation from column in a table.")
async def qlty_standardDeviation(
    table_name: str = Field(description="table name", default=""),
    col_name: str = Field(description="column name", default=""),
    ) -> ResponseType:
    """Get the standard deviation from column in a table."""
    return execute_db_tool( td.handle_qlty_standardDeviation, table_name=table_name, col_name=col_name)

@mcp.tool(description="Get the column summary statistics for a table.")
async def qlty_columnSummary(
    table_name: str = Field(description="table name", default=""),
    ) -> ResponseType:
    """Get the column summary statistics for a table."""
    return execute_db_tool( td.handle_qlty_columnSummary, table_name=table_name)

@mcp.tool(description="Get the univariate statistics for a table.")
async def qlty_univariateStatistics(
    table_name: str = Field(description="table name", default=""),
    col_name: str = Field(description="column name", default=""),
    ) -> ResponseType:
    """Get the univariate statistics for a table."""
    return execute_db_tool( td.handle_qlty_univariateStatistics, table_name=table_name, col_name=col_name)

@mcp.tool(description="Get the rows with missing values in a table.")
async def qlty_rowsWithMissingValues(
    table_name: str = Field(description="table name", default=""),
    col_name: str = Field(description="column name", default=""),
    ) -> ResponseType:
    """Get the rows with missing values in a table."""
    return execute_db_tool( td.handle_qlty_rowsWithMissingValues, table_name=table_name, col_name=col_name)

@mcp.prompt()
async def qlty_databaseQuality(database_name: str) -> UserMessage:
    """Assess the data quality of a database."""
    return UserMessage(role="user", content=TextContent(type="text", text=td.handle_qlty_databaseQuality.format(database_name=database_name)))

# ------------------ RAG Tools ------------------ #

@mcp.tool(description="""
Set the configuration for the current Retrieval-Augmented Generation (RAG) session.
This MUST be called before any other RAG-related tools.

The following values are hardcoded:
- query_table = 'user_query'
- query_embedding_store = 'user_query_embeddings'
- model_id = 'bge-small-en-v1.5'

You only need to provide the database locations:
- query_db: where user queries and query embeddings will be stored
- model_db: where the embedding model metadata is stored
- vector_db + vector_table: where PDF chunk embeddings are stored

Once this configuration is set, all other RAG tools will reuse it automatically.
""")
async def rag_setConfig(
    query_db: str = Field(description="Database to store user questions and query embeddings"),
    model_db: str = Field(description="Database where the embedding model is stored"),
    vector_db: str = Field(description="Database containing the chunk vector store"),
    vector_table: str = Field(description="Table containing chunk embeddings for similarity search"),
) -> ResponseType:
    return execute_db_tool( td.handle_rag_setConfig, query_db=query_db, model_db=model_db, vector_db=vector_db, vector_table=vector_table,)

@mcp.tool(
    description=(
        "Store a user's natural language question as the first step in a Retrieval-Augmented Generation (RAG) workflow."
        "This tool should always be run **before any embedding or similarity search** steps."
        "It inserts the raw question into a Teradata table specified by `db_name` and `table_name`. "
        "If the question starts with the prefix '/rag ', the prefix is automatically stripped before storage. "
        "Each question is appended as a new row with a generated ID and timestamp."
        "If the specified table does not exist, it will be created with columns: `id`, `txt`, and `created_ts`."
        "Returns the inserted row ID and cleaned question text."
        "This tool is **only needed once per user question** — downstream embedding and vector search tools "
        "can then reference this ID or re-use the stored question text."
    )
)
async def rag_storeUserQuery(
    db_name: str = Field(..., description="Name of the Teradata database where the question will be stored."),
    table_name: str = Field(..., description="Name of the table to store user questions (e.g., 'pdf_user_queries')."),
    question: str = Field(..., description="Natural language question from the user. Can optionally start with '/rag '."),
) -> ResponseType:
    return execute_db_tool( td.handle_rag_storeUserQuery, db_name=db_name, table_name=table_name, question=question)

@mcp.tool(
    description=(
        "Tokenizes the latest user-submitted question using the tokenizer specified in the current RAG configuration. "
        "This tool must be used *after* calling 'configure_rag' (to initialize the config) and 'store_user_query' (to capture a user question). "
        "It selects the most recent row from the query table (e.g., 'pdf_topics_of_interest'), runs it through the ONNX tokenizer, "
        "and creates a temporary view '<query_db>.v_topics_tokenized' containing 'id', 'txt', 'input_ids', and 'attention_mask'. "
        "This view is used downstream to generate vector embeddings for similarity search."
    )
)
async def rag_tokenizeQuery() -> ResponseType:
    return execute_db_tool( td.handle_rag_tokenizedQuery)

@mcp.tool(
    description=(
        "Generates sentence embeddings for the most recent tokenized user query using the model specified in the RAG configuration. "
        "Reads from the view `<db>.v_topics_tokenized` and applies the ONNX model from `<model_db>.embeddings_models`. "
        "Creates or replaces the view `<db>.v_topics_embeddings` which includes the original input and a `sentence_embedding` column. "
        "This must be run *after* create_tokenized_view and before vector_to_columns()."
    )
)
async def rag_createEmbeddingView() -> ResponseType:
    return execute_db_tool( td.handle_rag_createEmbeddingView)

@mcp.tool(
    description=(
        "Converts the sentence embedding from the view `v_topics_embeddings` into 384 vector columns using `ivsm.vector_to_columns`. "
        "Creates or replaces a physical table to store the latest query embeddings for use in similarity search. "
        "The table location is defined via `rag_set_config`. "
        "This tool must be run *after* `create_embedding_view` and before `semantic_search_chunks`."
    )
)
async def rag_createQueryEmbeddingTable() -> ResponseType:
    return execute_db_tool( td.handle_rag_createQueryEmbeddingTable)

@mcp.tool(
    description=(
        "Retrieve top-k most relevant PDF chunks for the user's latest embedded query. "
        "This tool is part of the RAG workflow and should be called after the query has been embedded. "
        "If the RAG config has not been set, use `rag_set_config` first to define where queries, models, and chunk embeddings are stored. "
        "Uses cosine similarity via `TD_VECTORDISTANCE` to compare embeddings. "
        "Each result includes similarity score, chunk text, page number, chunk number, and document name."
    )
)
async def rag_semanticSearchChunks(
    k: int = Field(10, description="Number of top matching chunks to retrieve."),
) -> ResponseType:
    return execute_db_tool( td.handle_rag_semanticSearchChunks, topk=k)


@mcp.prompt()
async def rag_guidelines() -> UserMessage:
    return UserMessage(role="user", content=TextContent(type="text", text=td.rag_guidelines))


#------------------ Security Tools  ------------------#

@mcp.tool(description="Get permissions for a user.")
async def sec_userDbPermissions(
    user_name: str = Field(description="User name", default=""),
    ) -> ResponseType:
    """Get permissions for a user."""
    return execute_db_tool( td.handle_sec_userDbPermissions, user_name=user_name)


@mcp.tool(description="Get permissions for a role.")
async def sec_rolePermissions(
    role_name: str = Field(description="Role name", default=""),
    ) -> ResponseType:
    """Get permissions for a role."""
    return execute_db_tool( td.handle_sec_rolePermissions, role_name=role_name)


@mcp.tool(description="Get roles assigned to a user.")
async def sec_userRoles(
    user_name: str = Field(description="User name", default=""),
    ) -> ResponseType:
    """Get roles assigned to a user."""
    return execute_db_tool( td.handle_sec_userRoles, user_name=user_name)





#------------------ Enterprise Vectore Store Tools  ------------------#

@mcp.tool(description="Enterprise Vector Store similarity search")
async def vector_store_similarity_search(
    question: str = Field(description="Natural language question"),
    top_k: int = Field(1, description="top matches to return"),
) -> ResponseType:

    return execute_vs_tool(
        td.evs_tools.handle_evs_similarity_search,
        question=question,
        top_k=top_k,
    )

@mcp.tool(description="Enterprise Vector Store ‑ best answer only")
async def vector_store_best_answer(
     question: str = Field(description="Natural language question"),
     top_k: int = Field(2, description="rows to inspect before picking best"),
 ) -> ResponseType:

     return execute_db_tool(
         td.evs_tools.handle_evs_similarity_search_getAnswerOnly,
         question=question,
         top_k=top_k,
     )


#--------------- Feature Store Tools ---------------#
# Feature tools leveraging the tdfs4ds package.


#---------- Feature Store Knowledge Base -----------#
class FeatureStoreConfig(BaseModel):
    """
    Configuration class for the feature store. This model defines the metadata and catalog sources 
    used to organize and access features, processes, and datasets across data domains.
    """

    data_domain: Optional[str] = Field(
        default=None,
        description="The data domain associated with the feature store, grouping features within the same namespace."
    )

    entity: Optional[str] = Field(
        default=None,
        description="The list of entities, comma separated and in alphabetical order, upper case."
    )

    db_name: Optional[str] = Field(
        default=None,
        description="Name of the database where the feature store is hosted."
    )

    feature_catalog: Optional[str] = Field(
        default=None,
        description=(
            "Name of the feature catalog table. "
            "This table contains detailed metadata about features and entities."
        )
    )

    process_catalog: Optional[str] = Field(
        default=None,
        description=(
            "Name of the process catalog table. "
            "Used to retrieve information about feature generation processes, features, and associated entities."
        )
    )

    dataset_catalog: Optional[str] = Field(
        default=None,
        description=(
            "Name of the dataset catalog table. "
            "Used to list and manage available datasets within the feature store."
        )
    )

fs_config = FeatureStoreConfig() 


@mcp.tool(description="Reconnect to the Teradata database if the connection is lost.")
async def reconnect_to_database() -> ResponseType:
    """Reconnect to Teradata database if connection is lost."""
    global _tdconn
    try:
        _tdconn = td.TDConn()
        td.teradataml_connection()
        return format_text_response("Reconnected to Teradata database successfully.")
    except Exception as e:
        logger.error(f"Error reconnecting to database: {e}")
        return format_error_response(str(e))


@mcp.tool(description="Set or update the feature store configuration (database and data domain).")
async def fs_setFeatureStoreConfig(
    data_domain: Optional[str] = None,
    db_name: Optional[str] = None,
    entity: Optional[str] = None,
) -> ResponseType:
    if db_name:
        if tdfs4ds.connect(database=db_name):
            logger.info(f"connected to the feature store of the {db_name} database")
            # Reset data_domain if DB name changes
            if not (fs_config.db_name and fs_config.db_name.upper() == db_name.upper()):
                fs_config.data_domain = None
            
            fs_config.db_name = db_name
            logger.info(f"connected to the feature store of the {db_name} database")
            fs_config.feature_catalog = f"{db_name}.{tdfs4ds.FEATURE_CATALOG_NAME_VIEW}"
            logger.info(f"feature catalog {fs_config.feature_catalog}")
            fs_config.process_catalog = f"{db_name}.{tdfs4ds.PROCESS_CATALOG_NAME_VIEW}"
            logger.info(f"process catalog {fs_config.process_catalog}")
            fs_config.dataset_catalog = f"{db_name}.FS_V_FS_DATASET_CATALOG"  # <- fixed line
            logger.info(f"dataset catalog {fs_config.dataset_catalog}")

    if fs_config.db_name is not None and data_domain is not None:
        sql_query_ = f"SEL count(*) AS N FROM {fs_config.feature_catalog} WHERE UPPER(data_domain) = '{data_domain.upper()}'"
        logger.info(f"{sql_query_}")
        result = tdml.execute_sql(sql_query_)
        logger.info(f"{result}")
        if result.fetchall()[0][0] > 0:
            fs_config.data_domain = data_domain
        else:
            fs_config.data_domain = None

    if fs_config.db_name is not None and fs_config.data_domain is not None and entity is not None:
        sql_query_ = f"SEL count(*) AS N FROM {fs_config.feature_catalog} WHERE UPPER(data_domain) = '{data_domain.upper()}' AND ENTITY_NAME = '{entity.upper()}'"
        logger.info(f"{sql_query_}")
        result = tdml.execute_sql(sql_query_)
        logger.info(f"{result}")
        if result.fetchall()[0][0] > 0:
            fs_config.entity = entity

    return format_text_response(f"Feature store config updated: {fs_config.dict(exclude_none=True)}")

@mcp.tool(description="Display the current feature store configuration (database and data domain).")
async def fs_getFeatureStoreConfig() -> ResponseType:
    return format_text_response(f"Current feature store config: {fs_config.dict(exclude_none=True)}")

@mcp.tool(
    description=(
        "Check if a feature store is present in the specified database."    
    )
)
async def fs_isFeatureStorePresent(
    db_name: str = Field(..., description="Name of the database to check for a feature store.")
) -> ResponseType:
    return execute_db_tool(td.handle_fs_isFeatureStorePresent, db_name=db_name)

@mcp.tool(
    description=(
        "Returns a summary of the feature store content."
        "Use this to understand what data is available in the feature store"
    )
)
async def fs_featureStoreContent(
) -> ResponseType:

    return execute_db_tool(td.handle_fs_featureStoreContent, fs_config=fs_config)

@mcp.tool(
    description=(
        "List the available data domains."
        "Requires a configured `db_name`  in the feature store config."
        "Use this to explore which entities can be used when building a dataset."
    )
)
async def fs_getDataDomains(
    entity: str = Field(..., description="The .")
) -> ResponseType:

    return execute_db_tool(td.handle_fs_getDataDomains, fs_config=fs_config)

@mcp.tool(
    description=(
        "List the list of features."
        "Requires a configured `db_name` and  `data_domain` in the feature store config."
        "Use this to explore the features available ."
    )
)
async def fs_getFeatures(
) -> ResponseType:

    return execute_db_tool(td.handle_fs_getFeatures, fs_config=fs_config)

@mcp.tool(
    description=(
        "List the list of available datasets."
        "Requires a configured `db_name` in the feature store config."
        "Use this to explore the datasets that are available ."
    )
)
async def fs_getAvailableDatasets(
) -> ResponseType:

    return execute_db_tool(td.handle_fs_getAvailableDatasets, fs_config=fs_config)

@mcp.tool(
    description=(
        "Return the schema of the feature store."
        "Requires a feature store in the configured database (`db_name`)."
        )
)
async def fs_getFeatureDataModel(
) -> ResponseType:
    return execute_db_tool(td.handle_fs_getFeatureDataModel, fs_config=fs_config)



@mcp.tool(
    description=(
        "List the available entities for a given data domain."
        "Requires a configured `db_name` and `data_domain` and  `entity` in the feature store config."
        "Use this to explore which entities can be used when building a dataset."
        )
)
async def fs_getAvailableEntities(
) -> ResponseType:
    return execute_db_tool(td.handle_fs_getAvailableEntities, fs_config=fs_config)

@mcp.tool(
    description=(
        "Create a dataset using selected features and an entity from the feature store."
        "The dataset is created in the specified target database under the given name."
        "Requires a configured feature store and data domain. Registers the dataset in the catalog automatically."
        "Use this when you want to build and register a new dataset for analysis or modeling."
        )
)
async def fs_createDataset(
    entity_name: str = Field(..., description="Entity for which the dataset will be created. Available entities are reported in the feature catalog."),
    feature_selection: list[str] = Field(..., description="List of features to include in the dataset. Available features are reported in the feature catalog."),
    dataset_name: str = Field(..., description="Name of the dataset to create."),
    target_database: str = Field(..., description="Target database where the dataset will be created.")
) -> ResponseType:
    return execute_db_tool(td.handle_fs_createDataset, fs_config=fs_config, entity_name=entity_name, feature_selection=feature_selection, dataset_name=dataset_name, target_database=target_database)

#------------------ Custom Tools  ------------------#
# Custom tools are defined as SQL queries in a YAML file and loaded at startup.


query_defs = []
custom_tool_files = [file for file in os.listdir() if file.endswith("_tools.yaml")]

for file in custom_tool_files:
    with open(file) as f:
        query_defs.extend(yaml.safe_load(f))  # Concatenate all query definitions


def make_custom_prompt(prompt: str, prompt_name: str, desc: str):
    async def _dynamic_prompt():
        # SQL is closed over without parameters
        return UserMessage(role="user", content=TextContent(type="text", text=prompt))
    _dynamic_prompt.__name__ = prompt_name
    return mcp.prompt(description=desc)(_dynamic_prompt)

def make_custom_query_tool(sql_text: str, tool_name: str, desc: str):
    async def _dynamic_tool():
        # SQL is closed over without parameters
        return execute_db_tool( td.handle_base_readQuery, sql=sql_text)
    _dynamic_tool.__name__ = tool_name
    return mcp.tool(description=desc)(_dynamic_tool)

# Instantiate custom query tools from YAML
for q in query_defs:
    if q["type"] == "tool":
        fn = make_custom_query_tool(q["sql"], q["name"], q.get("description", ""))
        globals()[q["name"]] = fn
        logger.info(f"Created custom tool: {q["name"]}")
    elif q["type"] == "prompt":
        fn = make_custom_prompt(q["prompt"], q["name"], q.get("description", "") )
        globals()[q["name"]] = fn
        logger.info(f"Created custom prompt: {q["name"]}")
    else:
        logger.info("Custom yaml type is unnkown.")


#------------------ Main ------------------#
# Main function to start the MCP server
#     Description: Initializes the MCP server and sets up signal handling for graceful shutdown.
#         It creates a connection to the Teradata database and starts the server to listen for incoming requests.
#         The function uses asyncio to manage asynchronous operations and handle signals for shutdown.
#         If an error occurs during initialization, it logs a warning message.
async def main():
    global _tdconn
    
    mcp_transport = os.getenv("MCP_TRANSPORT", "stdio").lower()
    logger.info(f"MCP_TRANSPORT: {mcp_transport}")

    # Set up proper shutdown handling
    try:
        loop = asyncio.get_running_loop()
        signals = (signal.SIGTERM, signal.SIGINT)
        for s in signals:
            logger.info(f"Registering signal handler for {s.name}")
            loop.add_signal_handler(s, lambda s=s: asyncio.create_task(shutdown(s)))
    except NotImplementedError:
        # Windows doesn't support signals properly
        logger.warning("Signal handling not supported on Windows")
        pass
    
    # Start the MCP server
    if mcp_transport == "sse":
        mcp.settings.host = os.getenv("MCP_HOST")
        mcp.settings.port = int(os.getenv("MCP_PORT"))
        open("/tmp/.alive", "w").close()
        logger.info(f"Starting MCP server on {mcp.settings.host}:{mcp.settings.port}")
        await mcp.run_sse_async()
    elif mcp_transport == "streamable-http":
        mcp.settings.host = os.getenv("MCP_HOST")
        mcp.settings.port = int(os.getenv("MCP_PORT"))
        mcp.settings.streamable_http_path = os.getenv("MCP_PATH", "/mcp/")
        logger.info(f"Starting MCP server on {mcp.settings.host}:{mcp.settings.port} with path {mcp.settings.streamable_http_path}")
        await mcp.run_streamable_http_async()
    else:
        logger.info("Starting MCP server on stdin/stdout")
        await mcp.run_stdio_async()    

#------------------ Shutdown ------------------#
# Shutdown function to handle cleanup and exit
#     Arguments: sig (signal.Signals) - signal received for shutdown
#     Description: Cleans up resources and exits the server gracefully.
#         It sets a flag to indicate that shutdown is in progress and logs the received signal.
#         If the shutdown is already in progress, it forces an immediate exit.
#         The function uses os._exit to terminate the process with a specific exit code.
async def shutdown(sig=None):
    """Clean shutdown of the server."""
    global shutdown_in_progress, _tdconn
    
    logger.info("Shutting down server")

    if os.path.exists("/tmp/.alive"):
        os.remove("/tmp/.alive")
    if shutdown_in_progress:
        logger.info("Forcing immediate exit")
        os._exit(1)  # Use immediate process termination instead of sys.exit
    
    _tdconn.close()
    shutdown_in_progress = True
    if sig:
        logger.info(f"Received exit signal {sig.name}")
    os._exit(128 + sig if sig is not None else 0)


#------------------ Entry Point ------------------#
# Entry point for the script
#     Description: This script is designed to be run as a standalone program.
#         It loads environment variables, initializes logging, and starts the MCP server.
#         The main function is called to start the server and handle incoming requests.
#         If an error occurs during execution, it logs the error and exits with a non-zero status code.
if __name__ == "__main__":
    asyncio.run(main())
