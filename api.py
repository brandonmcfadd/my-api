"""cta-reliability API by Brandon McFadden"""
from datetime import datetime, timedelta
from operator import index
import os  # Used to retrieve secrets in .env file
import time
import json
import logging
from logging.handlers import RotatingFileHandler
import secrets
import pandas as pd
from dotenv import load_dotenv  # Used to Load Env Var
from fastapi import FastAPI, HTTPException, Depends, status, Request, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import HTMLResponse, StreamingResponse, RedirectResponse, JSONResponse, PlainTextResponse
from fastapi.encoders import jsonable_encoder
import redis.asyncio as redis
from fastapi_limiter import FastAPILimiter
from fastapi_limiter.depends import RateLimiter
from google.cloud import bigquery
from google.oauth2 import service_account
from dateutil.relativedelta import relativedelta
import apihtml

app = FastAPI(docs_url=None)
security = HTTPBasic()

# Load .env variables
load_dotenv()

api_file_path = os.getenv('API_FILE_PATH')
main_file_path = os.getenv('FILE_PATH')
wmata_main_file_path = os.getenv('WMATA_FILE_PATH')
main_file_path_7000 = os.getenv('FILE_PATH_7000')
main_file_path_amtrak = os.getenv('FILE_PATH_AMTRAK')
main_file_path_transit_data = os.getenv('FILE_PATH_TRANSIT_DATA')
main_file_path_json = main_file_path + "train_arrivals/json/"
wmata_main_file_path_json = wmata_main_file_path + "train_arrivals/json/"
main_file_path_csv = main_file_path + "train_arrivals/csv/"
main_file_path_csv_month = main_file_path + "train_arrivals/csv_month/"
api_auth_token = os.getenv('API_AUTH_TOKEN')
api_auth_key = os.getenv('API_AUTH_KEY')
environment = os.getenv('ENVIRONMENT')
cta_train_arrivals_table = os.getenv('CTA_PROCESSED_ARRIVALS')
gcloud_project_id = os.getenv('GCLOUD_PROJECT_ID')
google_credentials_file = main_file_path + os.getenv('GOOGLE_APPLICATION_CREDENTIALS')


def get_date(date_type):
    """formatted date shortcut"""
    if date_type == "short":
        date = datetime.strftime(datetime.now(), "%Y%m%d")
    elif date_type == "hour":
        date = datetime.strftime(datetime.now(), "%H")
    elif date_type == "api-today":
        date = datetime.strftime(datetime.now(), "%Y-%m-%d")
    elif date_type == "api-yesterday":
        date = datetime.strftime(datetime.now()-timedelta(days=1), "%Y-%m-%d")
    elif date_type == "api-today-est":
        date = datetime.strftime(datetime.now()+timedelta(hours=1), "%Y-%m-%d")
    elif date_type == "api-yesterday-est":
        date = datetime.strftime(
            datetime.now()-timedelta(days=1)+timedelta(hours=1), "%Y-%m-%d")
    elif date_type == "api-last-month":
        date = datetime.strftime(
            datetime.now()-relativedelta(months=1), "%Y-%m")
    elif date_type == "api-last-month-est":
        date = datetime.strftime(
            datetime.now()-relativedelta(months=1)+timedelta(hours=1), "%Y-%m")
    elif date_type == "current":
        date = datetime.strftime(datetime.now(), "%d %b %Y %H:%M:%S")
    elif date_type == "code-time":
        date = datetime.strftime(datetime.now(), "%Y-%m-%dT%H:%M:%S%z")
    else:
        date = None
    return date


def get_current_username(credentials: HTTPBasicCredentials = Depends(security)):
    """Used to verify Creds"""
    file = open(file=api_file_path + '.tokens',
                mode='r',
                encoding='utf-8')
    tokens = json.load(file)
    try:
        if credentials.username in tokens:
            is_correct_username = True
        else:
            is_correct_username = False
            reason = "Incorrect username or password"
    except:  # pylint: disable=bare-except
        is_correct_username = False
        reason = "Incorrect username or password"

    try:
        if credentials.password == tokens[credentials.username]["password"]:
            is_correct_password = True
        else:
            is_correct_password = False
            reason = "Incorrect username or password"
    except:  # pylint: disable=bare-except
        is_correct_password = False
        reason = "Incorrect username or password"

    try:
        if tokens[credentials.username]["disabled"] == "True":
            is_enabled = False
            reason = "Account Disabled"
        else:
            is_enabled = True
    except:  # pylint: disable=bare-except
        is_enabled = True
        reason = "Account Disabled"

    if not (is_correct_username and is_correct_password and is_enabled):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=reason,
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def generate_html_response_intro():
    """Used for Root Page"""
    html_content = apihtml.MAIN_PAGE
    return HTMLResponse(content=html_content, status_code=200)


def generate_html_response_error(date, endpoint, current_time):
    """Used for Error Page"""
    html_content = f"""
    <html>
        <head>
            <title>CTA Reliability API Error</title>
        </head>
        <body>
            <h1>Error In CTA Reliability API Request</h1>
            <p>Current System Time: {current_time}</p>
            <p>Endpoint: {endpoint}{date}<br>
            Unable to retrieve results for the date {date}<br><br>
            If you are using the 'get_train_arrivals_by_day' endpoint, please note that data for the previous day is not loaded until ~01:00 CST.</p>
            <p></p>
            <p>Please refer to the documentation for assistance: <a href="https://brandonmcfadden.com">RTA API Documentation</a></p>
        </body>
    </html>
    """
    return HTMLResponse(content=html_content, status_code=200)


@app.on_event("startup")
async def startup():
    """Tells API to Prep redis for Rate Limit"""
    redis_value = redis.from_url(
        "redis://localhost", encoding="utf-8", decode_responses=True)
    # Logging Information
    logger = logging.getLogger("uvicorn.access")
    log_filename = api_file_path + '/logs/api-service.log'
    logging.basicConfig(level=logging.INFO)
    handler = RotatingFileHandler(log_filename, maxBytes=10e6, backupCount=10)
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    await FastAPILimiter.init(redis_value)


@app.get("/", dependencies=[Depends(RateLimiter(times=2, seconds=1))], response_class=RedirectResponse, status_code=302)
async def read_root():
    """Tells API to Display Root"""
    return "https://brandonmcfadden.com/transit-api"


@app.get("/api/", dependencies=[Depends(RateLimiter(times=2, seconds=1))], response_class=RedirectResponse, status_code=302)
async def documentation():
    """Tells API to Display Root"""
    return "https://brandonmcfadden.com/transit-api"


@app.get("/api/v1/get_daily_results/{date}", dependencies=[Depends(RateLimiter(times=2, seconds=1))])
async def return_results_for_date(date: str, token: str = Depends(get_current_username)):
    """Used to retrieve results"""
    try:
        json_file = main_file_path_json + "cta/" + date + ".json"
        results = open(json_file, 'r', encoding="utf-8")
        return Response(content=results.read(), media_type="application/json")
    except:  # pylint: disable=bare-except
        endpoint = "https://brandonmcfadden.com/api/v1/get_daily_results/"
        return generate_html_response_error(date, endpoint, get_date("current"))


@app.get("/api/v2/cta/get_daily_results/{date}", dependencies=[Depends(RateLimiter(times=2, seconds=1))])
async def return_results_for_date_cta_v2(date: str, token: str = Depends(get_current_username)):
    """Used to retrieve results"""
    if date == "today":
        date = get_date("api-today")
    elif date == "yesterday":
        date = get_date("api-yesterday")
    if date == "availability":
        files_available = sorted((f for f in os.listdir(
            main_file_path_json + "cta/") if not f.startswith(".")), key=str.lower)
        return files_available
    else:
        try:
            json_file = main_file_path_json + "cta/" + date + ".json"
            results = open(json_file, 'r', encoding="utf-8")
            return Response(content=results.read(), media_type="application/json")
        except:  # pylint: disable=bare-except
            endpoint = "https://brandonmcfadden.com/api/v2/cta/get_daily_results/"
            return generate_html_response_error(date, endpoint, get_date("current"))


@app.get("/api/v2/metra/get_daily_results/{date}", dependencies=[Depends(RateLimiter(times=2, seconds=1))])
async def return_results_for_date_metra_v2(date: str, token: str = Depends(get_current_username)):
    """Used to retrieve results"""
    if date == "today":
        date = get_date("api-today")
    elif date == "yesterday":
        date = get_date("api-yesterday")
    if date == "availability":
        files_available = sorted((f for f in os.listdir(
            main_file_path_json + "metra/") if not f.startswith(".")), key=str.lower)
        return files_available
    else:
        try:
            json_file = main_file_path_json + "metra/" + date + ".json"
            results = open(json_file, 'r', encoding="utf-8")
            return Response(content=results.read(), media_type="application/json")
        except:  # pylint: disable=bare-except
            endpoint = "https://brandonmcfadden.com/api/v2/metra/get_daily_results/"
            return generate_html_response_error(date, endpoint, get_date("current"))


@app.get("/api/v2/cta/get_train_arrivals_by_day/{date}", dependencies=[Depends(RateLimiter(times=2, seconds=1))])
async def return_arrivals_for_date_cta_v2(date: str, token: str = Depends(get_current_username)):
    """Used to retrieve results"""
    if date == "yesterday":
        date = get_date("api-yesterday")
    if date == "availability":
        files_available = sorted((f for f in os.listdir(
            main_file_path_csv + "cta/") if not f.startswith(".")), key=str.lower)
        return files_available
    else:
        try:
            csv_file = main_file_path_csv + "cta/" + date + ".csv"
            results = open(csv_file, 'r', encoding="utf-8")
            return StreamingResponse(
                results,
                media_type="text/csv",
                headers={
                    "Content-Disposition": f"attachment; filename=cta-arrivals-{date}.csv"}
            )
        except:  # pylint: disable=bare-except
            endpoint = "https://brandonmcfadden.com/api/v2/cta/get_train_arrivals_by_day/"
            return generate_html_response_error(date, endpoint, get_date("current"))


@app.get("/api/v2/cta/get_train_arrivals_by_month/{date}", dependencies=[Depends(RateLimiter(times=2, seconds=1))])
async def return_arrivals_for_date_month_cta_v2(date: str, token: str = Depends(get_current_username)):
    """Used to retrieve results"""
    if date == "yesterday":
        date = get_date("api-last-month")
    if date == "availability":
        files_available = sorted((f for f in os.listdir(
            main_file_path_csv_month + "cta/") if not f.startswith(".")), key=str.lower)
        return files_available
    else:
        try:
            csv_file = main_file_path_csv_month + "cta/" + date + ".csv"
            results = open(csv_file, 'r', encoding="utf-8")
            return StreamingResponse(
                results,
                media_type="text/csv",
                headers={
                    "Content-Disposition": f"attachment; filename=cta-arrivals-{date}.csv"}
            )
        except:  # pylint: disable=bare-except
            endpoint = "https://brandonmcfadden.com/api/v2/cta/get_train_arrivals_by_day/"
            return generate_html_response_error(date, endpoint, get_date("current"))


@app.get("/api/sorting_information/get", dependencies=[Depends(RateLimiter(times=2, seconds=1))], status_code=200)
async def get_sort_information(token: str = Depends(get_current_username)):
    """Used to retrieve results"""
    try:
        json_file = main_file_path + "sorting_information/sort_info.json"
        results = open(json_file, 'r', encoding="utf-8")
        return Response(content=results.read(), media_type="application/json")
    except:  # pylint: disable=bare-except
        endpoint = "https://brandonmcfadden.com/api/sorting_information/get"
        return generate_html_response_error(get_date("current"), endpoint, get_date("current"))


@app.get("/api/v2/wmata/get_daily_results/{date}", dependencies=[Depends(RateLimiter(times=2, seconds=1))])
async def return_results_for_date_wmata_v2(date: str, token: str = Depends(get_current_username)):
    """Used to retrieve results"""
    if date == "today":
        date = get_date("api-today-est")
    elif date == "yesterday":
        date = get_date("api-yesterday-est")
    if date == "availability":
        files_available = sorted((f for f in os.listdir(
            wmata_main_file_path_json) if not f.startswith(".")), key=str.lower)
        return files_available
    else:
        try:
            json_file = wmata_main_file_path_json + date + ".json"
            results = open(json_file, 'r', encoding="utf-8")
            return Response(content=results.read(), media_type="application/json")
        except:  # pylint: disable=bare-except
            endpoint = "https://brandonmcfadden.com/api/v2/wmata/get_daily_results/"
            return generate_html_response_error(date, endpoint, get_date("current"))


@app.get("/api/transit/get_daily_results/", dependencies=[Depends(RateLimiter(times=2, seconds=1))])
async def return_results_for_date_transit(agency: str, date: str = None, availability: bool = False, token: str = Depends(get_current_username)):
    """Used to retrieve results"""
    if date == "today" and (agency == 'cta' or agency == 'metra'):
        date = get_date("api-today")
    elif date == "yesterday" and (agency == 'cta' or agency == 'metra'):
        date = get_date("api-yesterday")
    if date == "today" and agency == "wmata":
        date = get_date("api-today-est")
    elif date == "yesterday" and agency == "wmata":
        date = get_date("api-yesterday-est")
    if availability is True and agency == 'cta':
        files_available = sorted((f for f in os.listdir(
            main_file_path_json + "cta/") if not f.startswith(".")), key=str.lower)
        return files_available
    elif availability is True and agency == "wmata":
        files_available = sorted((f for f in os.listdir(
            wmata_main_file_path_json) if not f.startswith(".")), key=str.lower)
        return files_available
    elif availability is True and agency == 'metra':
        files_available = sorted((f for f in os.listdir(
            main_file_path_json + "metra/") if not f.startswith(".")), key=str.lower)
        return files_available
    else:
        try:
            if agency == 'cta':
                json_file = main_file_path_json + "cta/" + date + ".json"
                results = open(json_file, 'r', encoding="utf-8")
                return Response(content=results.read(), media_type="application/json")
            if agency == 'metra':
                json_file = main_file_path_json + "metra/" + date + ".json"
                results = open(json_file, 'r', encoding="utf-8")
                return Response(content=results.read(), media_type="application/json")
            elif agency == "wmata":
                json_file = wmata_main_file_path_json + date + ".json"
                results = open(json_file, 'r', encoding="utf-8")
                return Response(content=results.read(), media_type="application/json")
            else:
                endpoint = "https://brandonmcfadden.com/api/transit/get_daily_results/"
                return generate_html_response_error(date, endpoint, get_date("current"))
        except:  # pylint: disable=bare-except
            endpoint = "https://brandonmcfadden.com/api/transit/get_daily_results/"
            return generate_html_response_error(date, endpoint, get_date("current"))


@app.get("/api/transit/get_train_arrivals_by_day/", dependencies=[Depends(RateLimiter(times=2, seconds=1))])
async def return_arrivals_for_date(agency: str, date: str = None, availability: bool = False, token: str = Depends(get_current_username)):
    """Used to retrieve results"""
    if date == "today" and (agency == 'cta' or agency == 'metra'):
        date = get_date("api-today")
    elif date == "yesterday" and (agency == 'cta' or agency == 'metra'):
        date = get_date("api-yesterday")
    if date == "today" and agency == "wmata":
        date = get_date("api-today-est")
    elif date == "yesterday" and agency == "wmata":
        date = get_date("api-yesterday-est")
    if availability is True and agency == "wmata":
        return "Unavailable"
    elif availability is True and agency == 'cta':
        files_available = sorted((f for f in os.listdir(
            main_file_path_csv + "cta/") if not f.startswith(".")), key=str.lower)
        return files_available
    elif availability is True and agency == 'metra':
        files_available = sorted((f for f in os.listdir(
            main_file_path_csv + "metra/") if not f.startswith(".")), key=str.lower)
        return files_available
    else:
        try:
            if agency == 'cta':
                csv_file = main_file_path_csv + "cta/" + date + ".csv"
                results = open(csv_file, 'r', encoding="utf-8")
                return StreamingResponse(
                    results,
                    media_type="text/csv",
                    headers={
                        "Content-Disposition": f"attachment; filename=cta-arrivals-{date}.csv"}
                )
            if agency == 'metra':
                csv_file = main_file_path_csv + "metra/" + date + ".csv"
                results = open(csv_file, 'r', encoding="utf-8")
                return StreamingResponse(
                    results,
                    media_type="text/csv",
                    headers={
                        "Content-Disposition": f"attachment; filename=cta-arrivals-{date}.csv"}
                )
            elif agency == "wmata":
                return "Unavailable"
            else:
                endpoint = "https://brandonmcfadden.com/api/transit/get_train_arrivals_by_day/"
                return generate_html_response_error(date, endpoint, get_date("current"))
        except:  # pylint: disable=bare-except
            endpoint = "https://brandonmcfadden.com/api/transit/get_train_arrivals_by_day/"
            return generate_html_response_error(date, endpoint, get_date("current"))


@app.get("/api/transit/get_train_arrivals/", dependencies=[Depends(RateLimiter(times=2, seconds=1))])
async def return_arrivals_for_dates(agency: str, startdate: str, enddate: str = None, token: str = Depends(get_current_username)):
    """Used to retrieve results"""
    if agency == "wmata" or agency == 'metra':
        return "Unavailable"
    elif enddate is None:
        enddate = get_date("api-today")
    try:
        if agency == 'cta':
            credentials = service_account.Credentials.from_service_account_file(
            google_credentials_file, scopes=[
                "https://www.googleapis.com/auth/cloud-platform"],
            )

            client = bigquery.Client(credentials=credentials,
                                project=credentials.project_id,)
            query_job = client.query(f"""
            SELECT * FROM `cta-utilities-410023.cta.processed_arrivals` 
            WHERE Arrival_Time >= '{startdate}' AND Arrival_Time < '{enddate}'
            ORDER BY Arrival_Time ASC""")
            results = query_job.to_dataframe(create_bqstorage_client=False) # Wait for the job to complete.
            return StreamingResponse(
                results.to_csv(index=False),
                media_type="text/csv",
                headers={
                    "Content-Disposition": f"attachment; filename=cta-arrivals-{startdate}-{enddate}.csv"}
            )
        else:
            endpoint = "https://brandonmcfadden.com/api/transit/get_train_arrivals/"
            return generate_html_response_error(get_date("current"), endpoint, get_date("current"))
    except:  # pylint: disable=bare-except
        endpoint = "https://brandonmcfadden.com/api/transit/get_train_arrivals/"
        return generate_html_response_error(get_date("current"), endpoint, get_date("current"))


@app.get("/api/transit/get_train_arrivals_by_month/", dependencies=[Depends(RateLimiter(times=2, seconds=1))])
async def return_arrivals_for_date_month(agency: str, date: str = None, availability: bool = False, token: str = Depends(get_current_username)):
    """Used to retrieve results"""
    if date == "today" and (agency == 'cta' or agency == 'metra'):
        date = get_date("api-today")
    elif date == "yesterday" and (agency == 'cta' or agency == 'metra'):
        date = get_date("api-last-month")
    if date == "today" and agency == "wmata":
        date = get_date("api-today-est")
    elif date == "yesterday" and agency == "wmata":
        date = get_date("api-last-month-est")
    if availability is True and agency == "wmata":
        return "Unavailable"
    elif availability is True and agency == 'cta':
        files_available = sorted((f for f in os.listdir(
            main_file_path_csv_month + "cta/") if not f.startswith(".")), key=str.lower)
        return files_available
    elif availability is True and agency == 'metra':
        files_available = sorted((f for f in os.listdir(
            main_file_path_csv_month + "metra/") if not f.startswith(".")), key=str.lower)
        return files_available
    else:
        try:
            if agency == 'cta':
                csv_file = main_file_path_csv_month + "cta/" + date + ".csv"
                results = open(csv_file, 'r', encoding="utf-8")
                return StreamingResponse(
                    results,
                    media_type="text/csv",
                    headers={
                        "Content-Disposition": f"attachment; filename=cta-arrivals-{date}.csv"}
                )
            if agency == 'metra':
                csv_file = main_file_path_csv_month + "metra/" + date + ".csv"
                results = open(csv_file, 'r', encoding="utf-8")
                return StreamingResponse(
                    results,
                    media_type="text/csv",
                    headers={
                        "Content-Disposition": f"attachment; filename=cta-arrivals-{date}.csv"}
                )
            elif agency == "wmata":
                return "Unavailable"
            else:
                endpoint = "https://brandonmcfadden.com/api/transit/get_train_arrivals_by_month/"
                return generate_html_response_error(date, endpoint, get_date("current"))
        except:  # pylint: disable=bare-except
            endpoint = "https://brandonmcfadden.com/api/transit/get_train_arrivals_by_month/"
            return generate_html_response_error(date, endpoint, get_date("current"))


@app.post("/api/user_management", dependencies=[Depends(RateLimiter(times=2, seconds=1))], status_code=200)
async def add_user_to_api(type: str, username: str, auth_token: str, token: str = Depends(get_current_username)):
    """Used to retrieve results"""
    try:
        if auth_token == api_auth_token:
            json_file = api_file_path + ".tokens"
            with open(json_file, 'r', encoding="utf-8") as fp:
                json_file_loaded = json.load(fp)
            if type == "add":
                password = secrets.token_urlsafe(32)
                input_data = {"password": password, "disabled": "False"}
                return_text = {"DateTime": get_date(
                    "code-time"), "Status": "", "Username": "", "Password": "", "Disabled": ""}
                if username in json_file_loaded:
                    return_text["Username"] = username
                    return_text["Status"] = "Exists"
                    return_text["Password"] = json_file_loaded[username]["password"]
                    return_text["Disabled"] = json_file_loaded[username]["disabled"]
                    json_file_loaded[username]["disabled"] = "False"
                else:
                    return_text["Username"] = username
                    return_text["Password"] = password
                    return_text["Disabled"] = "False"
                    return_text["Status"] = "Added"
                    json_file_loaded[username] = input_data
            elif type == "remove":
                if username in json_file_loaded:
                    json_file_loaded.pop(username, None)
                else:
                    return {"username": username, "Status": "Failed to Remove User. User Does Not Exist."}
                return_text = {"username": username, "Status": "Removed User."}
            with open(json_file, 'w', encoding="utf-8") as fp2:
                json.dump(json_file_loaded, fp2, indent=4,
                          separators=(',', ': '))
            return return_text
        else:
            endpoint = "https://brandonmcfadden.com/api/add_user"
            return generate_html_response_error(get_date("current"), endpoint, get_date("current"))
    except:  # pylint: disable=bare-except
        endpoint = "https://brandonmcfadden.com/api/add_user"
        return generate_html_response_error(get_date("current"), endpoint, get_date("current"))


@app.post("/api/amtrak/post", dependencies=[Depends(RateLimiter(times=2, seconds=1))], status_code=200)
async def amtrak_trips(response: Response, auth_token: str, type: str, date: str, train: str, origin: str = None, destination: str = None, service: str = None, token: str = Depends(get_current_username)):
    """Used to retrieve results"""
    try:
        if auth_token == api_auth_token:
            json_file = main_file_path_transit_data + "amtrak.json"
            with open(json_file, 'r', encoding="utf-8") as fp:
                json_file_loaded = json.load(fp)
            train_id = f"{date}-{train}"
            if type == "add":
                if train_id in json_file_loaded:
                    return_text = {"Status": "Train Already Present",
                                   "TrainDetails": json_file_loaded[train_id]}
                    response.status_code = status.HTTP_208_ALREADY_REPORTED
                else:
                    train_input = {"Date": date, "Train": train, "Origin": origin.upper(
                    ), "Destination": destination.upper(), "Service": service.capitalize()}
                    json_file_loaded[train_id] = train_input
                    return_text = {"Status": "Train Added",
                                   "TrainDetails": train_input}
                    response.status_code = status.HTTP_201_CREATED
            elif type == "remove":
                if train_id in json_file_loaded:
                    train_input = json_file_loaded[train_id]
                    json_file_loaded.pop(train_id, None)
                    return_text = {"Status": "Train Removed",
                                   "TrainDetails": train_input}
                    response.status_code = status.HTTP_202_ACCEPTED
                else:
                    return_text = {
                        "Status": "Failed to Remove Train. Train does not exist.", "TrainID": train_id}
                    response.status_code = status.HTTP_404_NOT_FOUND
            with open(json_file, 'w', encoding="utf-8") as fp2:
                json.dump(json_file_loaded, fp2, indent=4,
                          separators=(',', ': '))
            return return_text
        else:
            endpoint = "https://brandonmcfadden.com/api/amtrak/post/"
            return generate_html_response_error(get_date("current"), endpoint, get_date("current"))
    except:  # pylint: disable=bare-except
        endpoint = "https://brandonmcfadden.com/api/amtrak/post/"
        return generate_html_response_error(get_date("current"), endpoint, get_date("current"))


@app.get("/api/amtrak/get", dependencies=[Depends(RateLimiter(times=2, seconds=1))], status_code=200)
async def get_amtrak_trips(token: str = Depends(get_current_username)):
    """Used to retrieve results"""
    try:
        json_file = main_file_path_transit_data + "amtrak.json"
        results = open(json_file, 'r', encoding="utf-8")
        return Response(content=results.read(), media_type="application/json")
    except:  # pylint: disable=bare-except
        endpoint = "https://brandonmcfadden.com/api/amtrak/get/"
        return generate_html_response_error(get_date("current"), endpoint, get_date("current"))


@app.get("/api/transit-data/get", status_code=200)
async def get_transit_trips():
    """Used to retrieve results"""
    try:
        json_file = main_file_path_transit_data + "transit-data.json"
        results = open(json_file, 'r', encoding="utf-8")
        return Response(content=results.read(), media_type="application/json")
    except:  # pylint: disable=bare-except
        endpoint = "https://brandonmcfadden.com/api/transit-data/get/"
        return generate_html_response_error(get_date("current"), endpoint, get_date("current"))


@app.post("/api/transit-data/post", status_code=200)
async def transit_trips(request: Request, response: Response, auth_token: str, year: str, token: str = Depends(get_current_username)):
    """Used to retrieve results"""
    try:
        if auth_token == api_auth_token:
            json_file = main_file_path_transit_data + "transit-data.json"
            request_body_input = await request.json()
            with open(json_file, 'r', encoding="utf-8") as fp:
                json_file_loaded = json.load(fp)
            if year in json_file_loaded:
                json_file_loaded[year] = request_body_input
                response.status_code = status.HTTP_202_ACCEPTED
            else:
                json_file_loaded[year] = request_body_input
                response.status_code = status.HTTP_201_CREATED
            with open(json_file, 'w', encoding="utf-8") as fp2:
                json.dump(json_file_loaded, fp2, indent=4,
                          separators=(',', ': '))
            results = open(json_file, 'r', encoding="utf-8")
            return Response(content=results.read(), media_type="application/json")
        else:
            raise HTTPException(
                status_code=401, detail="Auth Token not Provided")
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail='Something Went Wrong') from exc


@app.post("/api/transit/post", dependencies=[Depends(RateLimiter(times=2, seconds=1))], status_code=200)
async def transit_tracker_trips(request: Request, response: Response, user: str, auth_token: str, type: str, agency: str):
    """Used to retrieve results"""
    try:
        if auth_token == api_auth_token:
            request_input = await request.json()
            if 'data' in request_input:
                request_input = request_input['data']
            elif 'body' in request_input:
                request_input = request_input['body']
            json_file = main_file_path_transit_data + "transit_trips.json"
            with open(json_file, 'r', encoding="utf-8") as fp:
                json_file_loaded = json.load(fp)
            train_id = f"{request_input['Date']}-{request_input['Route']}-{request_input['Run Number']}"
            username = user.upper()
            if username in json_file_loaded:
                if agency not in json_file_loaded[username]:
                    json_file_loaded[username][agency] = {}
                if type == "add":
                    if train_id in json_file_loaded[username][agency]:
                        return_text = {"Status": "Train Already Present",
                                    "TrainDetails": json_file_loaded[username][agency][train_id]}
                        response.status_code = status.HTTP_208_ALREADY_REPORTED
                    else:
                        loop_routes = ['Brown', 'Orange', 'Pink', 'Purple']
                        loop_stations = ['Clark/Lake', 'State/Lake', 'Washington/Wabash', 'Adams/Wabash',
                                        'Harold Washington Library', 'LaSalle/Van Buren', 'Quincy', 'Washington/Wells']
                        transit_stations_file_path = main_file_path_transit_data + \
                            "transit_stations.json"
                        with open(transit_stations_file_path, 'r', encoding="utf-8") as fp2:
                            transit_stations = json.load(fp2)
                        if request_input['Route'] in loop_routes and request_input['Origin'] in loop_stations:
                            request_input['Origin Station - Mileage'] = transit_stations[agency][request_input['Route']
                                                                                                ][request_input['Origin']]['Outbound']['Miles']
                            request_input['Origin Station - Kilometers'] = transit_stations[agency][request_input['Route']
                                                                                                    ][request_input['Origin']]['Outbound']['Kilometers']
                            request_input['Destination Station - Mileage'] = transit_stations[agency][request_input['Route']
                                                                                                    ][request_input['Destination']]['Outbound']['Miles']
                            request_input['Destination Station - Kilometers'] = transit_stations[agency][request_input['Route']
                                                                                                        ][request_input['Destination']]['Outbound']['Kilometers']
                        elif request_input['Route'] in loop_routes and request_input['Origin'] not in loop_stations:
                            request_input['Origin Station - Mileage'] = transit_stations[agency][request_input['Route']
                                                                                                ][request_input['Origin']]['Inbound']['Miles']
                            request_input['Origin Station - Kilometers'] = transit_stations[agency][request_input['Route']
                                                                                                    ][request_input['Origin']]['Inbound']['Kilometers']
                            request_input['Destination Station - Mileage'] = transit_stations[agency][request_input['Route']
                                                                                                    ][request_input['Destination']]['Inbound']['Miles']
                            request_input['Destination Station - Kilometers'] = transit_stations[agency][request_input['Route']
                                                                                                        ][request_input['Destination']]['Inbound']['Kilometers']
                        else:
                            request_input['Origin Station - Mileage'] = transit_stations[agency][request_input['Route']
                                                                                                ][request_input['Origin']]['Miles']
                            request_input['Origin Station - Kilometers'] = transit_stations[agency][request_input['Route']
                                                                                                    ][request_input['Origin']]['Kilometers']
                            request_input['Destination Station - Mileage'] = transit_stations[agency][request_input['Route']
                                                                                                    ][request_input['Destination']]['Miles']
                            request_input['Destination Station - Kilometers'] = transit_stations[agency][request_input['Route']
                                                                                                        ][request_input['Destination']]['Kilometers']
                        track_miles = round(request_input['Origin Station - Mileage'] -
                                            request_input['Destination Station - Mileage'], 2)
                        if track_miles < 0:
                            track_miles = track_miles * -1
                        track_kilometers = round(request_input['Origin Station - Kilometers'] -
                                                request_input['Destination Station - Kilometers'], 2)
                        if track_kilometers < 0:
                            track_kilometers = track_kilometers * -1
                        if agency == 'metra':
                            request_input['Origin Station - Zone'] = transit_stations[agency][request_input['Route']
                                                                                            ][request_input['Origin']]['Zone']
                            request_input['Destination Station - Zone'] = transit_stations[agency][request_input['Route']
                                                                                                ][request_input['Destination']]['Zone']
                            if (request_input['Origin Station - Zone'] in [2, 3, 4] and request_input['Destination Station - Zone'] in [2, 3, 4]) or (request_input['Origin Station - Zone'] in [1, 2] and request_input['Destination Station - Zone'] in [1, 2]):
                                if "Reduced" in request_input['Ticket Type']:
                                    trip_cost = 1.75
                                else:
                                    trip_cost = 3.75
                            elif (request_input['Origin Station - Zone'] in [1] and request_input['Destination Station - Zone'] in [3]) or (request_input['Origin Station - Zone'] in [3] and request_input['Destination Station - Zone'] in [1]):
                                if "Reduced" in request_input['Ticket Type']:
                                    trip_cost = 2.75
                                else:
                                    trip_cost = 5.50
                            elif (request_input['Origin Station - Zone'] in [1] and request_input['Destination Station - Zone'] in [4]) or (request_input['Origin Station - Zone'] in [4] and request_input['Destination Station - Zone'] in [1]):
                                if "Reduced" in request_input['Ticket Type']:
                                    trip_cost = 3.25
                                else:
                                    trip_cost = 6.75
                        elif agency == 'cta':
                            if request_input['Origin'] in ["O'Hare"]:
                                trip_cost = 5
                            else:
                                trip_cost = 2.5
                        elif agency == 'amtrak':
                            trip_cost = 0
                        elif agency == 'southshoreline':
                            request_input['Origin Station - Zone'] = transit_stations[agency][request_input['Route']
                                                                                            ][request_input['Origin']]['Zone']
                            request_input['Destination Station - Zone'] = transit_stations[agency][request_input['Route']
                                                                                                ][request_input['Destination']]['Zone']
                            trip_cost = 0
                        request_input['Track Miles'] = track_miles
                        request_input['Track Kilometers'] = track_kilometers
                        request_input['Trip Cost'] = trip_cost
                        json_file_loaded[username][agency][train_id] = request_input
                        return_text = {"Status": "Train Added",
                                    "Username": username,
                                    "TrainDetails": request_input}
                        response.status_code = status.HTTP_201_CREATED
                elif type == "remove":
                    if train_id in json_file_loaded[username][agency]:
                        train_input = json_file_loaded[username][agency][train_id]
                        json_file_loaded[username][agency].pop(train_id, None)
                        return_text = {"Status": "Train Removed",
                                    "Username": username,
                                    "TrainDetails": train_input}
                        response.status_code = status.HTTP_202_ACCEPTED
                    else:
                        return_text = {
                            "Status": "Failed to Remove Train. Train does not exist.", "TrainID": request_input}
                        response.status_code = status.HTTP_404_NOT_FOUND
                with open(json_file, 'w', encoding="utf-8") as fp2:
                    json.dump(json_file_loaded, fp2, indent=4,
                            separators=(',', ': '), sort_keys=True)
            else:
                return_text = {
                    "Status": "User Not Found - Unable to Proceed"}
                response.status_code = status.HTTP_404_NOT_FOUND
            return return_text
        else:
            raise HTTPException(
                status_code=400, detail='Something Went Wrong')
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail='Something Went Wrong') from exc


@app.get("/api/transit/get", dependencies=[Depends(RateLimiter(times=2, seconds=1))], status_code=200)
async def get_transit_tracker_trips(user: str, auth_token: str, output_type: str = "JSON"):
    """Used to retrieve results"""
    try:
        user_input = user.upper()
        if output_type.upper() == "JSON" and auth_token == api_auth_token:
            json_file = main_file_path_transit_data + "transit_trips.json"
            with open(json_file, 'r', encoding="utf-8") as fp:
                json_file_loaded = json.load(fp)
            if user_input == "ALL_USERS":
                return JSONResponse(content=jsonable_encoder(json_file_loaded))
            else:
                return JSONResponse(content=jsonable_encoder(json_file_loaded[user_input]))
        elif output_type.upper() == "CSV" and auth_token == api_auth_token:
            output_text = "User,Date,Agency,Route,RunNumber,Origin,Origin_Zone,Origin_Miles,Origin_Kilometers,Destination,Destination_Zone,Destination_Miles,Destination_Kilometers,Trip_Miles,Trip_Kilometers,Trip_Cost,Ticket_Type"
            json_file = main_file_path_transit_data + "transit_trips.json"
            with open(json_file, 'r', encoding="utf-8") as fp:
                json_file_loaded = json.load(fp)
            if user_input == "ALL_USERS":
                for username in json_file_loaded:
                    for agency_trip in json_file_loaded[username]:
                        for item in json_file_loaded[username][agency_trip]:
                            trip_cost = f"{json_file_loaded[username][agency_trip][item]['Trip Cost']:.2f}"
                            if agency_trip == 'metra':
                                new_line = f"{username},{json_file_loaded[username][agency_trip][item]['Date']},{agency_trip},{json_file_loaded[username][agency_trip][item]['Route']},{json_file_loaded[username][agency_trip][item]['Run Number']},{json_file_loaded[username][agency_trip][item]['Origin']},{json_file_loaded[username][agency_trip][item]['Origin Station - Zone']},{json_file_loaded[username][agency_trip][item]['Origin Station - Mileage']},{json_file_loaded[username][agency_trip][item]['Origin Station - Kilometers']},{json_file_loaded[username][agency_trip][item]['Destination']},{json_file_loaded[username][agency_trip][item]['Destination Station - Zone']},{json_file_loaded[username][agency_trip][item]['Destination Station - Mileage']},{json_file_loaded[username][agency_trip][item]['Destination Station - Kilometers']},{json_file_loaded[username][agency_trip][item]['Track Miles']},{json_file_loaded[username][agency_trip][item]['Track Kilometers']},{trip_cost},{json_file_loaded[username][agency_trip][item]['Ticket Type']}"
                            elif agency_trip in ['cta', 'amtrak', 'southshoreline']:
                                new_line = f"{username},{json_file_loaded[username][agency_trip][item]['Date']},{agency_trip},{json_file_loaded[username][agency_trip][item]['Route']},{json_file_loaded[username][agency_trip][item]['Run Number']},{json_file_loaded[username][agency_trip][item]['Origin']},,{json_file_loaded[username][agency_trip][item]['Origin Station - Mileage']},{json_file_loaded[username][agency_trip][item]['Origin Station - Kilometers']},{json_file_loaded[username][agency_trip][item]['Destination']},,{json_file_loaded[username][agency_trip][item]['Destination Station - Mileage']},{json_file_loaded[username][agency_trip][item]['Destination Station - Kilometers']},{json_file_loaded[username][agency_trip][item]['Track Miles']},{json_file_loaded[username][agency_trip][item]['Track Kilometers']},"
                            output_text = f"{output_text}\n{new_line}"
            elif user_input in json_file_loaded:
                for agency_trip in json_file_loaded[user_input]:
                    for item in json_file_loaded[user_input][agency_trip]:
                        trip_cost = f"{json_file_loaded[user_input][agency_trip][item]['Trip Cost']:.2f}"
                        if agency_trip == 'metra':
                            new_line = f"{user_input},{json_file_loaded[user_input][agency_trip][item]['Date']},{agency_trip},{json_file_loaded[user_input][agency_trip][item]['Route']},{json_file_loaded[user_input][agency_trip][item]['Run Number']},{json_file_loaded[user_input][agency_trip][item]['Origin']},,{json_file_loaded[user_input][agency_trip][item]['Origin Station - Mileage']},{json_file_loaded[user_input][agency_trip][item]['Origin Station - Kilometers']},{json_file_loaded[user_input][agency_trip][item]['Destination']},,{json_file_loaded[user_input][agency_trip][item]['Destination Station - Mileage']},{json_file_loaded[user_input][agency_trip][item]['Destination Station - Kilometers']},{json_file_loaded[user_input][agency_trip][item]['Track Miles']},{json_file_loaded[user_input][agency_trip][item]['Track Kilometers']},{trip_cost},{json_file_loaded[user_input][agency_trip][item]['Ticket Type']}"
                        elif agency_trip in ['cta', 'amtrak', 'southshoreline']:
                            new_line = f"{user_input},{json_file_loaded[user_input][agency_trip][item]['Date']},{agency_trip},{json_file_loaded[user_input][agency_trip][item]['Route']},{json_file_loaded[user_input][agency_trip][item]['Run Number']},{json_file_loaded[user_input][agency_trip][item]['Origin']},,{json_file_loaded[user_input][agency_trip][item]['Origin Station - Mileage']},{json_file_loaded[user_input][agency_trip][item]['Origin Station - Kilometers']},{json_file_loaded[user_input][agency_trip][item]['Destination']},,{json_file_loaded[user_input][agency_trip][item]['Destination Station - Mileage']},{json_file_loaded[user_input][agency_trip][item]['Destination Station - Kilometers']},{json_file_loaded[user_input][agency_trip][item]['Track Miles']},{json_file_loaded[user_input][agency_trip][item]['Track Kilometers']},{trip_cost},"
                        output_text = f"{output_text}\n{new_line}"
            else:
                raise HTTPException(
                    status_code=401, detail='User Not Found')
            return Response(content=output_text, media_type="text/csv", headers={
                "Content-Disposition": f"attachment; filename=transit-trips-{user_input}.csv"})
    except Exception as exc:
        raise HTTPException(
            status_code=404, detail='Unable to provide results') from exc


@app.post("/api/password_check", dependencies=[Depends(RateLimiter(times=2, seconds=1))], status_code=200)
async def transit_data_password_check(request: Request, response: Response):
    """Used to retrieve results"""
    try:
        file = open(file=api_file_path + '.transit_data_tokens',
                    mode='r',
                    encoding='utf-8')
        transit_tokens = json.load(file)
        request_input = await request.json()
        if request_input['Username'].upper() in transit_tokens:
            if request_input['Password'] == transit_tokens[request_input['Username'].upper()]:
                return_text = {"Status": "Valid Username and Password"}
                response.status_code = status.HTTP_202_ACCEPTED
            else:
                return_text = {"Status": "Incorrect Username or Password"}
                response.status_code = status.HTTP_401_UNAUTHORIZED
        else:
            return_text = {"Status": "Incorrect Username or Password"}
            response.status_code = status.HTTP_401_UNAUTHORIZED
        return return_text
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail='Something Went Wrong') from exc


@app.post("/api/transit/new-user", dependencies=[Depends(RateLimiter(times=2, seconds=1))], status_code=200)
async def transit_data_new_user(request: Request, response: Response):
    """Used to retrieve results"""
    try:
        file = open(file=api_file_path + '.transit_data_tokens',
                    mode='r',
                    encoding='utf-8')
        transit_tokens = json.load(file)
        request_input = await request.json()
        if 'data' in request_input:
            request_input = request_input['data']
        elif 'body' in request_input:
            request_input = request_input['body']
        if request_input['Username'].upper() in transit_tokens:
            return_text = {
                "Status": "User Already Exists. If you need a password change, contact Brandon :)"}
            response.status_code = status.HTTP_208_ALREADY_REPORTED
        else:
            json_file = main_file_path_transit_data + "transit_trips.json"
            with open(json_file, 'r', encoding="utf-8") as fp:
                json_file_loaded = json.load(fp)
            username = request_input['Username'].upper()
            password = request_input['Password']
            transit_tokens[username] = password
            json_file_loaded[username] = {}
            return_text = {"Status": "User Created",
                           "Username": username, "Password": password}
            response.status_code = status.HTTP_202_ACCEPTED
            with open(api_file_path + '.transit_data_tokens', 'w', encoding="utf-8") as fp2:
                json.dump(transit_tokens, fp2, indent=4,
                          separators=(',', ': '))
            with open(json_file, 'w', encoding="utf-8") as fp2:
                json.dump(json_file_loaded, fp2, indent=4,
                          separators=(',', ': '))
        return return_text
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail='Something Went Wrong') from exc


@app.get("/api/articles/get", status_code=200)
async def get_articles():
    """Used to retrieve results"""
    try:
        json_file = api_file_path + "data/articles.json"
        results = open(json_file, 'r', encoding="utf-8")
        return Response(content=results.read(), media_type="application/json")
    except:  # pylint: disable=bare-except
        endpoint = "https://brandonmcfadden.com/api/articles/get/"
        return generate_html_response_error(get_date("current"), endpoint, get_date("current"))


@app.post("/api/articles/post")
async def post_articles(request: Request, response: Response, auth_token: str, year: str, token: str = Depends(get_current_username)):
    """Used to retrieve results"""
    try:
        if auth_token == api_auth_token:
            json_file = api_file_path + "data/articles.json"
            request_body_input = await request.json()
            with open(json_file, 'r', encoding="utf-8") as fp:
                json_file_loaded = json.load(fp)
            if year in json_file_loaded:
                json_file_loaded[year].insert(0, request_body_input)
                response.status_code = status.HTTP_202_ACCEPTED
            else:
                json_file_loaded[year] = []
                json_file_loaded[year].insert(0, request_body_input)
                response.status_code = status.HTTP_201_CREATED
            with open(json_file, 'w', encoding="utf-8") as fp2:
                json.dump(json_file_loaded, fp2, indent=4,
                          separators=(',', ': '))
            results = open(json_file, 'r', encoding="utf-8")
            return Response(content=results.read(), media_type="application/json")
        else:
            raise HTTPException(
                status_code=401, detail="Auth Token not Provided")
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail='Something Went Wrong') from exc

@app.post("/api/tesla/post", response_class=PlainTextResponse)
async def post_battery_data(battery: str, miles: str, date: str, time: str, auth_token: str, response: Response):
    """Used to retrieve results"""
    try:
        if auth_token == api_auth_token:
            json_file = api_file_path + "data/tesla.json"
            input_json = {"Date":date,"Time":time,"Battery": battery,"MilesRemaining":miles}
            with open(json_file, 'r', encoding="utf-8") as fp:
                json_file_loaded = json.load(fp)

            last_entry = json_file_loaded[-1]
            last_entry_text = f"Last Entry Date:{last_entry['Date']} {last_entry['Time']}\nLast Entry: {last_entry['MilesRemaining']} miles ({last_entry['Battery']}%)"
            current_entry_text = f"New Entry Date:{input_json['Date']} {input_json['Time']}\nNew Entry: {miles} miles ({battery}%)"
            miles_different = str(int(miles)-int(last_entry["MilesRemaining"]))
            percent_different = str(int(battery)-int(last_entry["Battery"]))
            if int(miles_different) > 0:
                added_text_1 = "+"
            else:
                added_text_1 = ""
            if int(percent_different) > 0:
                added_text_2 = "+"
            else:
                added_text_2 = ""
            difference = f"Miles: {added_text_1}{miles_different}\nBattery: {added_text_2}{percent_different}%"
            combined_return_text = f"{last_entry_text}\n\n{current_entry_text}\n\n{difference}"
            json_file_loaded.append(input_json)
            response.status_code = status.HTTP_202_ACCEPTED
            with open(json_file, 'w', encoding="utf-8") as fp2:
                json.dump(json_file_loaded, fp2, indent=4,
                            separators=(',', ': '))
            return combined_return_text
        else:
            raise HTTPException(
                status_code=401, detail="Auth Token not Provided")
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail='Something Went Wrong') from exc

@app.get("/api/tesla/get", response_class=PlainTextResponse)
async def get_battery_data(entries: str, auth_token: str, response: Response):
    """Used to retrieve results"""
    try:
        if auth_token == api_auth_token:
            json_file = api_file_path + "data/tesla.json"
            with open(json_file, 'r', encoding="utf-8") as fp:
                json_file_loaded = json.load(fp)
            if entries == "all":
                entries_to_get = len(json_file_loaded)*-1
            elif int(entries) > len(json_file_loaded):
                entries_to_get = len(json_file_loaded)*-1
            else:
                entries_to_get = int(entries)*-1
            output = ""
            print(entries_to_get)
            while entries_to_get < 0:
                try:
                    last_entry = json_file_loaded[entries_to_get]
                    last_entry_text = f"Date:{last_entry['Date']} {last_entry['Time']} - {last_entry['MilesRemaining']} miles ({last_entry['Battery']}%)"
                    output = f"{last_entry_text}\n{output}"
                    entries_to_get += 1
                except:
                    entries_to_get += 1
                    continue
            response.status_code = status.HTTP_200_OK
            with open(json_file, 'w', encoding="utf-8") as fp2:
                json.dump(json_file_loaded, fp2, indent=4,
                            separators=(',', ': '))
            return output
        else:
            raise HTTPException(
                status_code=401, detail="Auth Token not Provided")
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail='Something Went Wrong') from exc

@app.post("/api/tesla/undo", response_class=PlainTextResponse)
async def undo_battery_data(auth_token: str, response: Response):
    """Used to retrieve results"""
    try:
        if auth_token == api_auth_token:
            json_file = api_file_path + "data/tesla.json"
            with open(json_file, 'r', encoding="utf-8") as fp:
                json_file_loaded = json.load(fp)

            last_entry = json_file_loaded[-1]
            last_entry_text = f"Last Entry Date:{last_entry['Date']} {last_entry['Time']}\nLast Entry: {last_entry['MilesRemaining']} miles ({last_entry['Battery']}%)"
            combined_return_text = f"Entry Removed:\n{last_entry_text}"
            response.status_code = status.HTTP_202_ACCEPTED
            del json_file_loaded[-1]
            with open(json_file, 'w', encoding="utf-8") as fp2:
                json.dump(json_file_loaded, fp2, indent=4,
                            separators=(',', ': '))
            return combined_return_text
        else:
            raise HTTPException(
                status_code=401, detail="Auth Token not Provided")
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail='Something Went Wrong') from exc
