var selectedJob = null;

async function showJobDetails(jobId) {
    if (selectedJob !== null) {
        selectedJob.classList.remove('job-item-selected');
    }
    console.log('Showing job details: ' + jobId); 
    var newSelectedJob = document.querySelector(`.job-item[data-job-id="${jobId}"]`);
    newSelectedJob.classList.add('job-item-selected');
    selectedJob = newSelectedJob;

    const response = await fetch('/job_details/' + jobId);
    const jobData = await response.json();

    updateJobDetails(jobData);

    if ('cover_letter' in jobData) {
        updateCoverLetter(jobData.cover_letter);
    } else {
        updateCoverLetter(null);
    }
}





function updateCoverLetter(coverLetter) {
    var coverLetterPane = document.getElementById('cover-letter-pane');
    // Check if the coverLetterPane exists
    if (coverLetterPane) {
        // Check if cover letter exists
        if (coverLetter === null) {
            coverLetterPane.innerText = 'No cover letter exists for this job.';
        } else {
            coverLetterPane.innerText = coverLetter;
        }
    }
}



function updateJobDetails(job) {
    var jobDetailsDiv = document.getElementById('job-details');
    var coverLetterDiv = document.getElementById('bottom-pane'); // Get the cover letter div
    console.log('Updating job details: ' + job.id); // Log the jobId here
    var html = '<h2 class="job-title">#' + job.id + ' - ' + job.title + '</h2>';
    html += '<div class="button-container" style="text-align:center">';
    html += '<a href="' + job.job_url + '" class="job-button">Go to job</a>';
    html += '<button class="job-button" onclick="markAsCoverLetter(' + job.id + ')">Cover Letter</button>';
    html += '<button class="job-button' + (job.applied == 1 ? ' active' : '') + '" onclick="markAsApplied(' + job.id + ', this)">Applied</button>';
    html += '<button class="job-button' + (job.rejected == 1 ? ' active' : '') + '" onclick="markAsRejected(' + job.id + ', this)">Rejected</button>';
    html += '<button class="job-button' + (job.interview == 1 ? ' active' : '') + '" onclick="markAsInterview(' + job.id + ', this)">Interview</button>';
    html += '<button class="job-button' + (job.hidden == 1 ? ' active' : '') + '" onclick="hideJob(' + job.id + ', this)">Hide</button>';
    html += '<button class="job-button danger" onclick="deleteJob(' + job.id + ')">Delete</button>';
    html += '</div>';
    html += '<p class="job-detail">' + job.company + ', ' + job.location + '</p>';
    html += '<p class="job-detail">' + job.date + '</p>';
    html += '<p class="job-description">' + job.job_description + '</p>';

    jobDetailsDiv.innerHTML = html;
    if (job.cover_letter) {
        // Update the cover letter div
        coverLetterDiv.innerHTML = '<p class="job-description">' + job.cover_letter + '</p>';
    } else {
        // Clear the cover letter div if no cover letter exists
        coverLetterDiv.innerHTML = '';
    }
}


// Shared toggle logic for the four status buttons (applied/rejected/interview/hidden).
// endpoint returns the NEW boolean state after flipping it server-side; this function
// reflects that state in both the button (active class) and the job-list card
// (job-item-<field> highlight), instead of always just adding the class.
function toggleStatus(jobId, endpoint, field, cardClass, buttonEl) {
    return fetch('/' + endpoint + '/' + jobId, { method: 'POST' })
        .then(response => response.json())
        .then(data => {
            console.log(data);
            if (!data.success) return data;
            var isOn = data[field];
            var jobCard = document.querySelector(`.job-item[data-job-id="${jobId}"]`);
            if (jobCard && cardClass) {
                jobCard.classList.toggle(cardClass, isOn);
            }
            if (buttonEl) {
                buttonEl.classList.toggle('active', isOn);
            }
            return data;
        });
}

function markAsApplied(jobId, buttonEl) {
    console.log('Toggling applied: ' + jobId);
    toggleStatus(jobId, 'mark_applied', 'applied', 'job-item-applied', buttonEl);
}

function markAsCoverLetter(jobId) {
    console.log('Marking job as cover letter: ' + jobId)
    fetch('/get_CoverLetter/' + jobId, { method: 'POST' })
        .then(response => response.json())
        .then(data => {
            console.log(data);  // Log the response
            if (data.cover_letter) {
                // Show the job details again, this will also update the cover letter
                showJobDetails(jobId);
            }
        });
}

function markAsRejected(jobId, buttonEl) {
    console.log('Toggling rejected: ' + jobId);
    toggleStatus(jobId, 'mark_rejected', 'rejected', 'job-item-rejected', buttonEl);
}

function findNextJobCard(jobCard) {
    var nextJobCard = jobCard.nextElementSibling;
    while (nextJobCard && !nextJobCard.classList.contains('job-item')) {
        nextJobCard = nextJobCard.nextElementSibling;
    }
    return nextJobCard;
}

function advancePastCard(jobCard) {
    var nextJobCard = findNextJobCard(jobCard);
    if (nextJobCard) {
        showJobDetails(nextJobCard.getAttribute('data-job-id'));
    } else {
        document.getElementById('job-details').innerHTML = '';
    }
    return nextJobCard;
}

function hideJob(jobId, buttonEl) {
    console.log('Toggling hidden: ' + jobId);
    fetch('/hide_job/' + jobId, { method: 'POST' })
        .then(response => response.json())
        .then(data => {
            if (!data.success) return;
            if (buttonEl) {
                buttonEl.classList.toggle('active', data.hidden);
            }
            var jobCard = document.querySelector(`.job-item[data-job-id="${jobId}"]`);
            if (data.hidden) {
                // Newly hidden: leave the view entirely, same as before.
                advancePastCard(jobCard);
                jobCard.style.display = 'none';
            } else {
                // Toggled back to visible (only reachable if something re-shows a
                // hidden job's details) - just clear the "hidden" look.
                jobCard.style.display = '';
            }
        });
}

function deleteJob(jobId) {
    if (!confirm('Delete job #' + jobId + ' permanently? This removes it from the database and cannot be undone.')) {
        return;
    }
    console.log('Deleting job: ' + jobId);
    fetch('/delete_job/' + jobId, { method: 'POST' })
        .then(response => response.json())
        .then(data => {
            if (!data.success) {
                alert('Could not delete job #' + jobId + '.');
                return;
            }
            var jobCard = document.querySelector(`.job-item[data-job-id="${jobId}"]`);
            advancePastCard(jobCard);
            jobCard.remove(); // actually gone from the DOM, not just hidden
        });
}


function markAsInterview(jobId, buttonEl) {
    console.log('Toggling interview: ' + jobId);
    toggleStatus(jobId, 'mark_interview', 'interview', 'job-item-interview', buttonEl);
}

var resizer = document.getElementById('resizer');
var jobDetails = document.getElementById('job-details');
var bottomPane = document.getElementById('bottom-pane');
var originalHeight, originalMouseY;

resizer.addEventListener('mousedown', function(e) {
    e.preventDefault();
    originalHeight = jobDetails.getBoundingClientRect().height;
    originalMouseY = e.pageY;
    document.addEventListener('mousemove', drag);
    document.addEventListener('mouseup', stopDrag);
});

function drag(e) {
    var delta = e.pageY - originalMouseY;
    jobDetails.style.height = (originalHeight + delta) + "px";
    bottomPane.style.height = `calc(100% - ${originalHeight + delta}px - 10px)`;
}

function stopDrag() {
    document.removeEventListener('mousemove', drag);
    document.removeEventListener('mouseup', stopDrag);
}