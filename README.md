IsMyWebsitFast

This is a simple tool that uses the Google PSI api and Claude api that then analyzes the speed of any given websites and returns a report with the performance score.

How it works

There is a textbox where url(s) can be given, and then the Google api is called, and it retries upto 3 times. Then it will take the performance score and the core web vitals that api returns using JSON. It will then keep track of each analysis to a local SQLite file so that you can track the score over time. And it loops over like this through every link and returns the score for each one. After that, the Claude api is used to summarize the data and then display for the user to either copy or download.

The files

The main.py is the core of the code where everything runs and works. The code here calls the apis, and then processes their outputs to then change and format/summarize into infomation the user then can use or download. The app.py on the other hand is the frontend. This is what creates the streamlit app that will allow the user to use the program.

For my AI usage, I only used it a bit for assistance and debugging along the way while I was coding. I also used AI to conveniently push code from the editor directly to the github repo as noted in the contributors. So I believe I used AI below the 30% limit.
