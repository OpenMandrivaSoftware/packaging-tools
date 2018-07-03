#include <KArchive>
#include <KTar>
#include <KZip>
#include <K7Zip>
#include <QString>

#include <iostream>

static bool debug = false;

static QStringList findProvides(KArchiveDirectory const &dir);
static QStringList findProvides(KArchiveDirectory const &dir) {
	QStringList provs;
	for(QString const &s: dir.entries()) {
		KArchiveEntry const *e = dir.entry(s);
		if(e->isDirectory()) {
			provs << findProvides(static_cast<KArchiveDirectory const &>(*e));
			continue;
		}
		if(s.toLower().startsWith("find") && s.toLower().endsWith(".cmake"))
			provs << QString("cmake(" + s.mid(4, s.length()-10) + ")");
		else if(s.toLower().endsWith("-config.cmake"))
			provs << QString("cmake(" + s.left(s.length()-13) + ")");
		else if(s.toLower().endsWith("config.cmake"))
			provs << QString("cmake(" + s.left(s.length()-12) + ")");
	}
	provs.removeDuplicates();
	return provs;
}

static QStringList findDeps(KArchiveDirectory const &dir);
static QStringList findDeps(KArchiveDirectory const &dir) {
	QStringList deps;

	#define addDep(x) \
		if (debug) \
			std::cerr << "\t\t===> " << qPrintable(QString(x)) << std::endl; \
		deps << QString(x);

	for(QString const &s: dir.entries()) {
		KArchiveEntry const *e = dir.entry(s);
		if(e->isDirectory()) {
			for(QString const &dep : findDeps(static_cast<KArchiveDirectory const &>(*e)))
				if(!deps.contains(dep))
					deps << dep;
			continue;
		}
		if(!e->isFile())
			continue;
		if(s.toLower() == "cmakelists.txt" || s.toLower().endsWith(".cmake")) {
			KArchiveFile const *f = static_cast<KArchiveFile const *>(e);
			QStringList const c=QString(f->data()).split("\n");
			for(QStringList::ConstIterator it=c.begin(); it!=c.end(); ++it) {
				QString line = *it;
				while(line.count("(") != line.count(")")) {
					if(++it == c.end())
						break;
					line += *it;
				}
				line = line.simplified();
				if(debug)
					std::cerr << qPrintable(line) << std::endl;
				if(line.toLower().startsWith("find_package(") || line.toLower().startsWith("find_package (")) {
					QString dep = line.section('(', 1).section(')', 0, 0);
					if(debug)
						std::cerr << "\t" << qPrintable(dep) << std::endl;
					QStringList args = dep.split(' ');
					// We're nowhere near smart enough to handle a dependency on
					// something defined in a cmake variable...
					if(args[0].contains('$')) {
						continue;
					}
					bool submodules = false;
					for(int i=1; i<args.size(); i++) {
						if(args[i].startsWith('#'))
							break;
						else if(args[i].startsWith('$') || args[i] == "NO_MODULE" || args[i] == "CONFIG" || args[i] == "REQUIRED" || args[i] == "COMPONENTS")
							continue;
						else if(args[i].front() >= '0' && args[i].front() <= '9')
							// The rpm Provides: generator doesn't support
							// versioning well enough to care
							continue;
						else {
							// This is a Qt/KDE convention, but not necessarily
							// true for everything else...
							if(args[0] == "Qt5" || args[0] == "KF5") {
								addDep("cmake(" + args[0] + args[i] + ")");
							} else {
								addDep("cmake(" + args[i] + ")");
							}
							submodules = true;
						}
					}
					if(!submodules) {
						addDep("cmake(" + args[0] + ")");
					}
				}
				if(it == c.end())
					break;
			}
		}
	}
	deps.removeDuplicates();
	return deps;
}


int main(int argc, char **argv) {
	QString file;
	for(int i=1; i<argc; i++) {
		QString arg=argv[i];
		if(arg.startsWith('-')) {
			if(arg == "-d" || arg == "--debug")
				debug = true;
			else {
				std::cerr << "WARNING: Unknown option: " << qPrintable(arg) << std::endl;
			}
		} else if(!file.isEmpty()) {
			std::cerr << "Usage:" << std::endl
				<< argv[0] << " sourcetarball.tar.xz" << std::endl;
			return 1;
		} else
			file=arg;
	}
	if(file.isEmpty()) {
		std::cerr << "Usage:" << std::endl
			<< argv[0] << " sourcetarball.tar.xz" << std::endl;
		return 2;
	}
	KArchive *ar;
	if(file.toLower().endsWith(".zip"))
		ar = new KZip(file);
	else if(file.toLower().endsWith(".7z"))
		ar = new K7Zip(file);
	else
		ar = new KTar(file);

	if(!ar) {
		std::cerr << "Error creating KArchive instance" << std::endl;
		return 3;
	}

	if(!ar->open(QIODevice::ReadOnly)) {
		std::cerr << "Can't open " << qPrintable(file) << ": " << qPrintable(ar->errorString()) << std::endl;
		return 4;
	}

	KArchiveDirectory const *d = ar->directory();
	if(!d) {
		std::cerr << "Can't find directory in archive" << std::endl;
		return 5;
	}

	QStringList BRs = findDeps(*d);
	QStringList provides = findProvides(*d);

	// We don't want to depend on ourselves...
	for(QString const &dep : provides)
		BRs.removeAll(dep);

	for(QString const &dep : BRs)
		std::cout << "BuildRequires: " << qPrintable(dep) << std::endl;

	return 0;
}
