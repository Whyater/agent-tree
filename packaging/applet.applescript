-- Agent Tree.app: a stay-open applet that runs the local server while the app is open.
on launcherPath()
	return quoted form of (POSIX path of (path to me) & "Contents/Resources/launcher.sh")
end launcherPath

on run
	do shell script launcherPath() & " start > /dev/null 2>&1 &"
end run

on reopen
	do shell script launcherPath() & " open"
end reopen

on quit
	try
		do shell script launcherPath() & " stop"
	end try
	continue quit
end quit
