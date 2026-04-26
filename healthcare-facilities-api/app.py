"""
Healthcare Facilities Trust Scoring API
Optimized for Databricks Apps deployment
"""

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import os

# Try to import databricks-sql-connector
try:
    from databricks import sql
    HAS_SQL_CONNECTOR = True
except ImportError:
    HAS_SQL_CONNECTOR = False
    print("Warning: databricks-sql-connector not available")

app = FastAPI(
    title="Healthcare Facilities Trust Scoring API",
    description="REST API for healthcare facility discovery with trust scores and geospatial search",
    version="1.0.0"
)

# CORS - Update with your frontend domain in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: Replace with your frontend domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# === Configuration ===
# For Databricks Apps, these can come from environment variables OR app secrets
SERVER_HOSTNAME = os.getenv("DATABRICKS_SERVER_HOSTNAME", "")
HTTP_PATH = os.getenv("DATABRICKS_HTTP_PATH", "")
ACCESS_TOKEN = os.getenv("DATABRICKS_TOKEN", "")

# Table name
TABLE_NAME = "hacknation.default.api_facilities_for_map"

# === Database Connection ===
def get_db_connection():
    """Create database connection using environment variables"""
    if not all([SERVER_HOSTNAME, HTTP_PATH, ACCESS_TOKEN]):
        raise ValueError(
            "Missing required configuration. Please set:\n"
            "- DATABRICKS_SERVER_HOSTNAME\n"
            "- DATABRICKS_HTTP_PATH\n"
            "- DATABRICKS_TOKEN"
        )
    
    if not HAS_SQL_CONNECTOR:
        raise ImportError("databricks-sql-connector not installed")
    
    return sql.connect(
        server_hostname=SERVER_HOSTNAME,
        http_path=HTTP_PATH,
        access_token=ACCESS_TOKEN
    )

def execute_query(query: str, params: dict = None):
    """Execute SQL query and return results"""
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        
        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)
        
        columns = [desc[0] for desc in cursor.description]
        results = cursor.fetchall()
        
        cursor.close()
        connection.close()
        
        return [dict(zip(columns, row)) for row in results]
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

# === Pydantic Models ===
class FacilityResponse(BaseModel):
    facility_name: str
    facility_type: Optional[str]
    latitude: float
    longitude: float
    city: Optional[str]
    state: Optional[str]
    pincode: Optional[str]
    trust_score: int
    risk_category: str
    trust_reason: Optional[str]
    map_marker_color: str
    phone: Optional[str]
    website: Optional[str]
    has_facebook: bool
    has_twitter: bool
    has_linkedin: bool

class SearchResponse(BaseModel):
    count: int
    facilities: List[FacilityResponse]

class StatsResponse(BaseModel):
    total_facilities: int
    avg_trust_score: float
    by_risk_category: dict

# === API Endpoints ===

@app.get("/")
async def root():
    """Health check endpoint"""
    config_status = "configured" if all([SERVER_HOSTNAME, HTTP_PATH, ACCESS_TOKEN]) else "not configured"
    return {
        "status": "healthy",
        "service": "Healthcare Facilities API",
        "version": "1.0.0",
        "config": config_status
    }

@app.get("/api/v1/facilities/search/city", response_model=SearchResponse)
async def search_by_city(
    city: str = Query(..., description="City name to search"),
    limit: int = Query(100, ge=1, le=500, description="Maximum results to return")
):
    """Search facilities by city name"""
    query = f"""
    SELECT * FROM {TABLE_NAME}
    WHERE LOWER(city) = LOWER(%(city)s)
    ORDER BY trust_score DESC
    LIMIT %(limit)s
    """
    
    results = execute_query(query, {"city": city, "limit": limit})
    
    return {
        "count": len(results),
        "facilities": results
    }

@app.get("/api/v1/facilities/search/pincode", response_model=SearchResponse)
async def search_by_pincode(
    pincode: str = Query(..., description="Pincode to search"),
    limit: int = Query(100, ge=1, le=500)
):
    """Search facilities by postal code"""
    query = f"""
    SELECT * FROM {TABLE_NAME}
    WHERE pincode = %(pincode)s
    ORDER BY trust_score DESC
    LIMIT %(limit)s
    """
    
    results = execute_query(query, {"pincode": pincode, "limit": limit})
    
    return {
        "count": len(results),
        "facilities": results
    }

@app.get("/api/v1/facilities/search/bounds", response_model=SearchResponse)
async def search_by_bounds(
    min_lat: float = Query(..., description="Minimum latitude"),
    max_lat: float = Query(..., description="Maximum latitude"),
    min_lon: float = Query(..., description="Minimum longitude"),
    max_lon: float = Query(..., description="Maximum longitude"),
    limit: int = Query(500, ge=1, le=1000)
):
    """Search facilities within map viewport bounds"""
    query = f"""
    SELECT * FROM {TABLE_NAME}
    WHERE latitude BETWEEN %(min_lat)s AND %(max_lat)s
      AND longitude BETWEEN %(min_lon)s AND %(max_lon)s
    ORDER BY trust_score DESC
    LIMIT %(limit)s
    """
    
    results = execute_query(query, {
        "min_lat": min_lat,
        "max_lat": max_lat,
        "min_lon": min_lon,
        "max_lon": max_lon,
        "limit": limit
    })
    
    return {
        "count": len(results),
        "facilities": results
    }

@app.get("/api/v1/facilities/search/radius", response_model=SearchResponse)
async def search_by_radius(
    lat: float = Query(..., description="Center latitude"),
    lon: float = Query(..., description="Center longitude"),
    radius_km: float = Query(..., ge=0.1, le=100, description="Search radius in kilometers"),
    limit: int = Query(100, ge=1, le=500)
):
    """Search facilities within radius using H3 proximity"""
    # Using haversine distance formula
    query = f"""
    SELECT *,
        2 * 6371 * ASIN(SQRT(
            POW(SIN((%(lat)s - latitude) * PI() / 180 / 2), 2) +
            COS(%(lat)s * PI() / 180) * COS(latitude * PI() / 180) *
            POW(SIN((%(lon)s - longitude) * PI() / 180 / 2), 2)
        )) AS distance_km
    FROM {TABLE_NAME}
    WHERE h3_index_res7 IS NOT NULL
    HAVING distance_km <= %(radius_km)s
    ORDER BY distance_km ASC, trust_score DESC
    LIMIT %(limit)s
    """
    
    results = execute_query(query, {
        "lat": lat,
        "lon": lon,
        "radius_km": radius_km,
        "limit": limit
    })
    
    # Remove distance_km from response
    for result in results:
        result.pop('distance_km', None)
    
    return {
        "count": len(results),
        "facilities": results
    }

@app.get("/api/v1/facilities/detail/{facility_name}")
async def get_facility_detail(facility_name: str):
    """Get detailed information for a specific facility"""
    query = f"""
    SELECT * FROM {TABLE_NAME}
    WHERE facility_name = %(facility_name)s
    LIMIT 1
    """
    
    results = execute_query(query, {"facility_name": facility_name})
    
    if not results:
        raise HTTPException(status_code=404, detail="Facility not found")
    
    return results[0]

@app.get("/api/v1/facilities/stats", response_model=StatsResponse)
async def get_statistics():
    """Get overall statistics about facilities"""
    query = f"""
    SELECT 
        COUNT(*) as total_facilities,
        AVG(trust_score) as avg_trust_score,
        risk_category,
        COUNT(*) as category_count
    FROM {TABLE_NAME}
    GROUP BY risk_category
    """
    
    results = execute_query(query)
    
    total = sum(r['category_count'] for r in results)
    avg_score = sum(r['avg_trust_score'] * r['category_count'] for r in results) / total if total > 0 else 0
    
    by_category = {r['risk_category']: r['category_count'] for r in results}
    
    return {
        "total_facilities": total,
        "avg_trust_score": round(avg_score, 2),
        "by_risk_category": by_category
    }

@app.get("/api/v1/facilities/cities")
async def get_cities(limit: int = Query(50, ge=1, le=200)):
    """Get list of cities with facility counts"""
    query = f"""
    SELECT 
        city,
        state,
        COUNT(*) as facility_count,
        AVG(trust_score) as avg_trust_score
    FROM {TABLE_NAME}
    WHERE city IS NOT NULL
    GROUP BY city, state
    ORDER BY facility_count DESC
    LIMIT %(limit)s
    """
    
    results = execute_query(query, {"limit": limit})
    
    return {
        "count": len(results),
        "cities": results
    }

# For local testing
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
