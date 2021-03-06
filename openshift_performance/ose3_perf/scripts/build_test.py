import re, subprocess, json, time, threading
import datetime,random
from optparse import OptionParser


def run(cmd,config=""):

    if config:
        cmd = "KUBECONFIG=" + config + " " + cmd
    result = subprocess.check_output(cmd,stderr=subprocess.STDOUT,shell=True)
    return result

def login(url, user, passwd):
    run("oc login --insecure-skip-tls-verify=true -u " + user + " -p " + passwd + " " + url)


def run_build(build_def, start_build):
    global build_stats

    date_fmt = "%H:%M:%S"

    namespace = build_def["namespace"]
    name = build_def["name"]

    try:

        # wait at starting line for other builder threads
        start_build.wait()
        build_name = run("oc start-build -n " + namespace + " " + name).rstrip()
        build_qname = namespace + ":" + build_name
        print "\nBuild is: " + namespace + ":" + build_name
        build_completed = False
        build_time = -1
        retry_completion_check = 10

        # wait for build to go into phase = "Complete"
        while  build_completed != True:
            time.sleep(retry_completion_check)
            result = run("oc get -o json -n " + namespace + " build/" + build_name)
            build_info = json.loads(result)
            if build_info and build_info["status"] and build_info["status"]["phase"]:
                if build_info["status"]["phase"] == "Complete":
                    build_completed=True
                    if "duration" in build_info["status"]:
                        build_time = build_info["status"]["duration"]/1000000000
                    else:
                        build_time = 0
                        print "ERROR: Duration missing from " + build_qname
                    result = run("oc logs -n " + namespace + " build/" + build_name)

                    # timestamps for start and end of push.  End of push message different for non-s2i builds
                    push_start = re.search(".*(\d\d:\d\d:\d\d).\d+\s+\d\s\S+\sPushing",result).group(1)
                    push_end = re.search(".*(\d\d:\d\d:\d\d).\d+\s+\d\s\S+\s(Successfully pushed|Push successful)",result).group(1)

                    #calculate and record stats
                    push_time_delta = datetime.datetime.strptime(push_end, date_fmt) - datetime.datetime.strptime(push_start, date_fmt)
                    push_time = push_time_delta.total_seconds()

                    print "\nBuild completed: " + build_qname + " Build time: " + str(build_time) + " Push time: " + str(push_time)
                    idx = namespace + ":" + name

                    if (build_time == 0) or (push_time == 0):
                        print "Invalid data - not included in summary statistics"
                        build_stats[idx]["invalid"] += 1
                    else:
                        build_stats[idx]["num"] += 1
                        build_stats[idx]["total_time"] += build_time
                        build_stats[idx]["push_time"] += push_time
                        if build_time > build_stats[idx]["max_total"]:
                            build_stats[idx]["max_total"] = build_time
                        if push_time > build_stats[idx]["max_push"]:
                            build_stats[idx]["max_push"] = push_time
                elif (build_info["status"]["phase"] == "Failed") or (build_info["status"]["phase"] == "Cancelled"):
                    print build_qname + " FAILED"
                    idx = namespace + ":" + name
                    build_stats[idx]["failed"] += 1
                    build_completed = True
            else:
                print "Unable to retreive status for: " + build_name

    except subprocess.CalledProcessError as err:
        #print "Command failed:  tproject=" + namespace + ", aproject=" + run("oc project",kubeconfig) + ",cmd=" + err.cmd + ", retcode=" + str(err.returncode) + ", output=" + err.output
        print "Command failed:  tproject=" + namespace +  ",cmd=" + err.cmd + ", retcode=" + str(err.returncode) + ", output=" + err.output




    return build_stats

# Run builds one at a time
def run_builds_sequentially(all_builds, sleep_time):
    for i in range(1,3):
        print i
        for build in all_builds:
            run_build(build)

#Select a random set of builds to run.  Duplicates not allowed
def select_random_builds(builds, num):
    selected_builds = []
    seen = set()
    i=0
    while i < num:
        chosen_build = random.choice(builds)
        build_idx = chosen_build["namespace"] + ":" + chosen_build["name"]
        if build_idx not in seen:
            seen.add(build_idx)
            selected_builds.append(chosen_build)
            i += 1
    return selected_builds



#Run builds simultaneously in background threads
def run_builds(all_builds):
    global globalconfig

    start_all_builds=threading.Event()
    for i in range(0,globalconfig["num_iterations"]):
        print str(datetime.datetime.now()).split('.')[0]+ ": iteration: " + str(i + 1)
        if globalconfig["random"] > 0:
            selected_builds = select_random_builds(all_builds, globalconfig["random"])
        else:
            selected_builds = all_builds
        thread_list=[]
        start_all_builds.clear()

        for build in selected_builds:
            t = threading.Thread(target=run_build,args=(build,start_all_builds,))
            thread_list.append(t)
            try:
                t.start()
            except Exception as errtxt:
                print errtxt

        #final sleep before triggering the start event, probably not necessary
        time.sleep(5)
        print "All threads started, starting builds and joining"
        start_all_builds.set()
        for t in thread_list:
            t.join()
        print "All threads joined.  Sleeping " + str(globalconfig["sleep_time"]) + " before next iteration\n"
        time.sleep(int(globalconfig["sleep_time"]))

def get_build_configs():
    all_builds=[]

    build_configs = json.loads(run("oc get --all-namespaces=true -o json bc"))
    if build_configs:
        for build in build_configs["items"]:
            all_builds.append({"namespace":build["metadata"]["namespace"],"name":build["metadata"]["name"]})
    return all_builds

if __name__ ==  "__main__":

    parser = OptionParser()
    parser.add_option("-m", "--master", dest="master",
                     help="url of the OpenShift master to login to")
    parser.add_option("-u", "--user", dest="oseuser",
                     help="OpenShift user for login")
    parser.add_option("-p", "--password", dest="osepass",
                     help="OpenShift password for login")
    parser.add_option("-a", "--all", dest="allbuilds", action="store_true",
                     help="Run all builds in all projects")
    parser.add_option("-f", "--file", dest="buildfile",
                     help="JSON file with builds to run")
    parser.add_option("-n", "--numiter", dest="num", default=1,
                     help="Number of iterations")
    parser.add_option("-s", "--sleep", dest="sleep", default=0,
                     help="Seconds to sleep between iterations")
    parser.add_option("-r", "--random", dest="random", default=0,
                      help="Number of builds to select randomly on each iteration")
    parser.add_option("-d", "--debug", dest="debug", action="store_true",
                     help="Debug messages")
    globalconfig = {}
    build_stats = {}
    random.seed()

    (options, args) = parser.parse_args()
    globalconfig["master"] = options.master
    globalconfig["oseuser"] = options.oseuser
    globalconfig["password"] = options.osepass
    globalconfig["all_builds"] = options.allbuilds
    globalconfig["build_file"] = options.buildfile
    globalconfig["num_iterations"] = int(options.num)
    globalconfig["sleep_time"] = int(options.sleep)
    globalconfig["debug"] = options.debug
    globalconfig["random"] = int(options.random)

    login(globalconfig["master"],globalconfig["oseuser"],globalconfig["password"])

    print "Gathering build info..."

    if globalconfig["all_builds"] == True:
        all_builds = get_build_configs()
    elif globalconfig["build_file"] :
        with open(globalconfig["build_file"]) as f:
            builds = f.read().replace('\n', '')
            all_builds = json.loads(builds)


    print "Build info gathered."

    #init statistics
    for build in all_builds:
        idx=build["namespace"] + ":" + build["name"]
        build_stats[idx]={"num":0, "total_time": 0, "max_total":0, "push_time": 0, "max_push":0, "total_time_variance":0, "push_time_variance":0, "failed":0, "invalid":0}

    run_builds(all_builds)

    #output stats
    total_all_builds=0
    total_all_pushes=0
    total_failed=0
    total_invalid=0
    total_builds=0
    for build in all_builds:
        idx = build["namespace"] + ":" + build["name"]
        num = build_stats[idx]["num"]
        print "\nBuild: " + idx
        if num > 0:
            print "\tTotal builds: " + str(build_stats[idx]["num"]) + " Failures: " + str(build_stats[idx]["failed"])
            print "\tAvg build time: " + str(build_stats[idx]["total_time"]/num) +  " Max build time: " + str(build_stats[idx]["max_total"])
            print "\tAvg push time: " + str(build_stats[idx]["push_time"]/num)+  " Max push time: " + str(build_stats[idx]["max_push"])
            total_all_builds += build_stats[idx]["total_time"]
            total_all_pushes += build_stats[idx]["push_time"]
            total_builds += build_stats[idx]["num"]
        else:
            print "\tNo successful builds"
        total_failed += build_stats[idx]["failed"]
        total_invalid += build_stats[idx]["invalid"]

    if total_builds > 0:
        print "\n\nFailed builds: " + str(total_failed)
        print "\nInvalid builds: " + str(total_invalid)
        print "\nGood builds included in stats: " + str(total_builds)
        print "\nAverage build time, all good builds: " + str(total_all_builds/total_builds)
        print "\nAverage push time, all good builds: " + str(total_all_pushes/total_builds)