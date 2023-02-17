from datetime import datetime
import os
from flask import Flask, render_template, request, redirect, url_for, send_from_directory

from bs4 import BeautifulSoup
import requests
from lxml import html
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from dotenv import load_dotenv
import uuid

import openai

import concurrent.futures

from azure.cosmos import CosmosClient, PartitionKey

app = Flask(__name__)

# Retrieve the Cosmos DB endpoint and key from environment variables
COSMOS_ENDPOINT = os.environ.get('ACCOUNT_HOST')
COSMOS_KEY = os.environ.get('ACCOUNT_KEY')

# Initialize the Cosmos DB client
client = CosmosClient(COSMOS_ENDPOINT, COSMOS_KEY)

# Create a new database and container if they don't already exist
database_name = os.environ.get('DATABASE_NAME')
container_name = os.environ.get('CONTAINER_NAME')
database = client.create_database_if_not_exists(id=database_name)
container = database.create_container_if_not_exists(
    id=container_name,
    partition_key=PartitionKey(path='/id')
)

# Define a function to store the scraped text in the Cosmos DB container
def store_scraped_text(id, url, text):
    # Create a new document with the URL and text
    document = {'id': id, 'url': url, 'text': text}
    
    # Insert the document into the Cosmos DB container
    container.create_item(body=document)
    
    print(f'Scraped text for URL {url} stored successfully.')

def summarize(prompt):
    response = openai.Completion.create(
        model="text-davinci-003",
        prompt=prompt,
        temperature=0.7,
        max_tokens=3090,
        top_p=1,
        frequency_penalty=0,
        presence_penalty=0,
        stop=["\\n\\n"]
    )
    return response.choices[0].text.strip()

load_dotenv()

def get_text_from_web(url):
    # Set up a headless Chrome browser
    chrome_options = Options()
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36')
    chrome_options.add_argument('accept-language=en-US,en;q=0.8')
    chrome_options.add_argument('upgrade-insecure-requests=1')
    chrome_options.add_argument('sec-fetch-dest=document')
    chrome_options.add_argument('sec-fetch-mode=navigate')
    chrome_options.add_argument('sec-fetch-site=same-origin')
    service = Service('chromedriver') # Replace with path to chromedriver executable
    driver = webdriver.Chrome(service=service, options=chrome_options)

    # Load the webpage with Selenium
    driver.get(url)

    # Wait for the JavaScript to load
    wait = WebDriverWait(driver, 10)
    bill_text_container = wait.until(EC.presence_of_element_located((By.ID, 'billTextContainer')))
    
    # Extract the HTML content and parse it with BeautifulSoup
    html = driver.page_source
    print(html)
    soup = BeautifulSoup(html, 'html.parser')
    bill_text_container = soup.find('pre', {'id': 'billTextContainer'})
    if bill_text_container:
        bill_text = bill_text_container.get_text()
        return bill_text
    else:
        return ('Could not find bill text container.')

@app.route('/')
def index():
   print('Request for index page received')
   return render_template('index.html')

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'),
                               'favicon.ico', mimetype='image/vnd.microsoft.icon')

@app.route('/summary', methods=['POST'])
def summary():
    name = request.form.get('name')
    bill_text = get_text_from_web(name)
    
    # Call the store_scraped_text function with the URL and text to store them in Cosmos DB
    store_scraped_text(str(uuid.uuid4()), name, bill_text)
    
    tokenized_text = bill_text.split('\n\n')
    filtered_tokenized_text = [item for item in tokenized_text if not item.startswith('[[Page')]

    openai.api_key = os.getenv("OPENAI_API_KEY")

    summaries = []
    with concurrent.futures.ThreadPoolExecutor() as executor:
        prompt_list = [
            "You're a consultant and have been provided the following legislation to provide a summary CFO. You need to highlight all of the financial information. If there is no financial information in the section then briefly identify the section as having no relevant financial information. The text of the bill is: {}".format(section)
            for section in filtered_tokenized_text[400:500]
        ]
        results = executor.map(summarize, prompt_list)
        summaries.extend(list(results))

    if name:
        print('Request for summary page received with url=%s' % name)
        return render_template('summary.html', summary=summaries)
    else:
        print('Request for summary page received with no url or blank url -- redirecting')
        return redirect(url_for('index'))


if __name__ == '__main__':
   app.run()